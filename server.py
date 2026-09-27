"""Online video -> CC:Tweaked frame/audio bridge. Python 3.10+."""
import argparse
import json
import ipaddress
import socket
import importlib.util
import os
import secrets
import hmac
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from PIL import Image

PORT = 8765
FPS = 8
MAX_SECONDS = 300
MAX_PIXELS = 32000
# The CC:Tweaked default palette, in blit order (0..f).
PALETTE = [
    (240,240,240), (242,178,51), (229,127,216), (153,178,242),
    (222,222,108), (127,204,25), (242,178,204), (76,76,76),
    (153,153,153), (76,153,178), (178,102,229), (51,102,204),
    (127,102,76), (87,166,78), (204,76,76), (17,17,17),
]
HEX = b'0123456789abcdef'

def video_filter(width, height, fps):
    physical_w, physical_h = width * 6, height * 9
    return (f'fps={fps},eq=contrast=1.06:saturation=1.18,'
            f'scale={physical_w}:{physical_h}:force_original_aspect_ratio=decrease:flags=lanczos,'
            f'pad={physical_w}:{physical_h}:(ow-iw)/2:(oh-ih)/2:color=black,'
            f'scale={width}:{height}:flags=lanczos')

def make_palette(palette):
    image = Image.new('P', (1, 1))
    values = [v for rgb in palette for v in rgb]
    image.putpalette(values + [0] * (768 - len(values)))
    return image

def sampled_palette(source, width, height):
    frame_size = width * height * 3
    cmd = ['ffmpeg', '-v', 'error', '-i', str(source), '-t', '30',
           '-vf', video_filter(width, height, 1), '-frames:v', '30',
           '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-an', '-']
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    samples = []
    try:
        while len(samples) < 30:
            frame = proc.stdout.read(frame_size)
            if len(frame) != frame_size:
                break
            samples.append(frame)
    finally:
        proc.stdout.close()
        proc.wait(timeout=20)
    if not samples or proc.returncode:
        raise RuntimeError('Nao foi possivel analisar as cores do video.')
    # One palette for the entire video avoids palette flicker between frames.
    montage = Image.frombytes('RGB', (width, height * len(samples)), b''.join(samples))
    reduced = montage.quantize(colors=15, method=Image.Quantize.MEDIANCUT)
    raw = reduced.getpalette()
    palette = [tuple(raw[i:i+3]) for i in range(0, 45, 3)]
    palette.append((0, 0, 0))
    return palette

lock = threading.Lock()
job = {'state': 'idle'}
job_dir = None
job_cancel = None
ACCESS_TOKEN = os.environ.get('CC_TV_TOKEN') or secrets.token_urlsafe(24)


def validated_url(url):
    if not isinstance(url, str) or len(url) > 1900 or any(ord(c) < 32 for c in url):
        raise ValueError('Link invalido ou longo demais.')
    try:
        p = urlparse(url)
        host = p.hostname
        port = p.port
    except ValueError as ex:
        raise ValueError('Link invalido.') from ex
    if p.scheme not in ('http', 'https') or not host or p.username or p.password:
        raise ValueError('Use um link HTTP ou HTTPS publico, sem login na URL.')
    if port not in (None, 80, 443):
        raise ValueError('Porta do link nao permitida.')
    try:
        addresses = socket.getaddrinfo(host, port or 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            raise ValueError('Links para rede local nao sao permitidos.')
    except socket.gaierror as ex:
        raise ValueError('Dominio do link nao encontrado.') from ex
    return url


def run(cmd, **kwargs):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, **kwargs)


def convert(url, width, height, fps, folder, cancel):
    command = [sys.executable, '-m', 'yt_dlp', '--no-playlist',
         '--no-progress', '--no-warnings',
         '--match-filter', f'duration <=? {MAX_SECONDS} & !is_live',
         '--max-filesize', '300M', '-f', 'bv*[height<=480]+ba/b[height<=480]/b/bv*+ba/b',
         '--merge-output-format', 'mkv', '-o', str(folder / 'source.%(ext)s'), url]
    download = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, errors='replace')
    try:
        while True:
            try:
                output, errors = download.communicate(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                if cancel.is_set():
                    download.terminate()
                    download.communicate()
                    raise RuntimeError('Conversao cancelada.')
    finally:
        if download.poll() is None:
            download.kill()
            download.communicate()
    if download.returncode:
        raise RuntimeError('yt-dlp: ' + (errors or output)[-1200:])
    candidates = [p for p in folder.glob('source.*') if p.is_file() and p.suffix not in ('.part', '.ytdl', '.json')]
    if not candidates:
        detail = (output + '\n' + errors).strip()[-1200:]
        raise RuntimeError('yt-dlp nao gerou video. ' + (detail or 'Verifique o link.'))
    source = candidates[0]
    if cancel.is_set():
        raise RuntimeError('Conversao cancelada.')
    palette = sampled_palette(source, width, height)
    cc_palette = make_palette(palette)
    count = 0
    frame_size = width * height * 3
    cmd = ['ffmpeg', '-v', 'error', '-i', str(source), '-t', str(MAX_SECONDS),
           '-vf', video_filter(width, height, fps),
           '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-an', '-']
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        with (folder / 'frames.bin').open('wb') as dst:
            while count < fps * MAX_SECONDS:
                if cancel.is_set():
                    proc.terminate()
                    break
                frame = proc.stdout.read(frame_size)
                if len(frame) != frame_size:
                    break
                img = Image.frombytes('RGB', (width, height), frame)
                # Fast nearest-palette conversion; one ASCII nibble per pixel.
                quant = img.quantize(palette=cc_palette, dither=Image.Dither.FLOYDSTEINBERG)
                dst.write(bytes(HEX[min(i, 15)] for i in quant.tobytes()))
                count += 1
    finally:
        proc.stdout.close()
        proc.wait(timeout=20)
    if cancel.is_set():
        raise RuntimeError('Conversao cancelada.')
    if proc.returncode != 0 or count == 0:
        raise RuntimeError('FFmpeg nao conseguiu converter o video.')
    audio = folder / 'audio.dfpwm'
    if cancel.is_set():
        raise RuntimeError('Conversao cancelada.')
    try:
        run(['ffmpeg', '-y', '-v', 'error', '-i', str(source), '-t', str(MAX_SECONDS),
             '-vn', '-ac', '1', '-ar', '48000', '-c:a', 'dfpwm', '-f', 'dfpwm', str(audio)], timeout=240)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        audio.unlink(missing_ok=True)
    source.unlink(missing_ok=True)
    return count, audio.exists(), ['#%02x%02x%02x' % rgb for rgb in palette]


def worker(generation, url, width, height, fps, folder, cancel):
    global job_dir
    try:
        count, has_audio, palette = convert(url, width, height, fps, folder, cancel)
        with lock:
            if job.get('generation') == generation:
                job.update(state='ready', frames=count, audio=has_audio, palette=palette)
    except Exception as ex:
        detail = str(ex)
        if isinstance(ex, subprocess.CalledProcessError) and ex.stderr:
            detail = ex.stderr.decode('utf-8', errors='replace')[-700:]
        if not cancel.is_set():
            print('Falha na conversao: ' + detail, flush=True)
        with lock:
            if job.get('generation') == generation:
                job.update(state='error', message=detail)
    finally:
        with lock:
            active = job.get('generation') == generation
        if not active:
            shutil.rmtree(folder, ignore_errors=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print('%s %s' % (self.address_string(), fmt % args))

    def send(self, code, data, content_type='application/json'):
        if isinstance(data, dict):
            data = json.dumps(data).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def authorized(self):
        provided = self.headers.get('Authorization', '')
        if not hmac.compare_digest(provided, 'Bearer ' + ACCESS_TOKEN):
            self.send(401, {'error': 'Chave de acesso incorreta.'})
            return False
        return True

    def do_POST(self):
        if not self.authorized():
            return
        global job_dir, job_cancel
        if self.path == '/cancel':
            with lock:
                if job_cancel: job_cancel.set()
                old = job_dir if job['state'] == 'ready' else None
                job['generation'] = job.get('generation', 0) + 1
                job['state'] = 'idle'
                job_dir = None
            if old: shutil.rmtree(old, ignore_errors=True)
            return self.send(200, {'state': 'idle'})
        if self.path != '/start':
            return self.send(404, {'error': 'Rota inexistente'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 2048:
                raise ValueError('Pedido muito grande ou vazio.')
            req = json.loads(self.rfile.read(size))
            video_url = validated_url(req['url'])
            width, height = int(req['width']), int(req['height'])
            fps = int(req.get('fps', FPS))
            if not (4 <= width <= 320 and 4 <= height <= 160 and width * height <= MAX_PIXELS):
                raise ValueError('Resolucao invalida ou grande demais.')
            if not 1 <= fps <= 10:
                raise ValueError('FPS deve ser entre 1 e 10.')
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as ex:
            return self.send(400, {'error': str(ex)})
        with lock:
            if job_cancel: job_cancel.set()
            old = job_dir if job['state'] != 'loading' else None
            folder = Path(tempfile.mkdtemp(prefix='cc_tv_'))
            job_dir = folder
            job_cancel = threading.Event()
            generation = job.get('generation', 0) + 1
            job.clear()
            job.update(state='loading', generation=generation, width=width, height=height, fps=fps)
        if old:
            shutil.rmtree(old, ignore_errors=True)
        threading.Thread(target=worker, args=(generation, video_url, width, height, fps, folder, job_cancel), daemon=True).start()
        self.send(202, {'state': 'loading'})

    def do_GET(self):
        if not self.authorized():
            return
        p = urlparse(self.path)
        if p.path not in ('/status', '/frame', '/audio'):
            return self.send(404, {'error': 'Rota inexistente'})
        with lock:
            status = {k:v for k,v in job.items() if k != 'generation'}
            folder = job_dir
        if p.path == '/status':
            return self.send(200, status)
        if status['state'] != 'ready' or not folder:
            return self.send(409, {'error': 'Video ainda nao esta pronto.'})
        try:
            query = parse_qs(p.query)
            n = int(query.get('n', ['-1'])[0])
            count = int(query.get('count', ['1'])[0])
            if not 1 <= count <= 8:
                raise ValueError('Lote deve ter entre 1 e 8 partes.')
            if p.path == '/frame':
                if count * status['width'] * status['height'] > 96000:
                    raise ValueError('Lote de quadros grande demais para esta resolucao.')
                if not 0 <= n < status['frames']:
                    raise ValueError('Quadro fora do intervalo.')
                with (folder / 'frames.bin').open('rb') as f:
                    f.seek(n * status['width'] * status['height'])
                    data = f.read(min(count, status['frames'] - n) * status['width'] * status['height'])
            else:
                if not status['audio'] or not 0 <= n <= MAX_SECONDS * 48000 // 8 // 6144 + 1:
                    raise ValueError('Audio indisponivel ou indice invalido.')
                with (folder / 'audio.dfpwm').open('rb') as f:
                    f.seek(n * 6144)
                    data = f.read(count * 6144)
            return self.send(200, data, 'application/octet-stream')
        except (ValueError, OSError) as ex:
            return self.send(400, {'error': str(ex)})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1', help='IP local; use 0.0.0.0 so em rede privada confiavel')
    parser.add_argument('--port', type=int, default=PORT)
    args = parser.parse_args()
    if importlib.util.find_spec('yt_dlp') is None:
        parser.error('Instale as dependencias: py -m pip install -r requirements.txt')
    if not shutil.which('ffmpeg'):
        parser.error('ffmpeg nao encontrado no PATH')
    try:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as ex:
        if ex.errno == 98 or ex.errno == 10048:
            parser.error(f'Porta {args.port} ja em uso. Verifique: systemctl --user status cc-tv.service; ss -ltnp sport = :{args.port}')
        raise
    print(f'Servidor iniciado em http://{args.host}:{args.port} (Ctrl+C para parar)', flush=True)
    print(f'CHAVE DE ACESSO: {ACCESS_TOKEN}', flush=True)
    with server:
        server.serve_forever()

if __name__ == '__main__':
    main()
