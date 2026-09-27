"""Local YouTube -> CC:Tweaked frame/audio bridge. Python 3.10+."""
import argparse
import json
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
MAX_SECONDS = 120
MAX_PIXELS = 12000
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
ACCESS_TOKEN = os.environ.get('CC_TV_TOKEN') or secrets.token_urlsafe(24)


def video_id(url):
    p = urlparse(url)
    host = (p.hostname or '').lower()
    if p.scheme != 'https' or p.username or p.password or p.port:
        raise ValueError('Use um link HTTPS normal do YouTube.')
    if host in ('youtube.com', 'www.youtube.com', 'm.youtube.com'):
        if p.path == '/watch':
            value = parse_qs(p.query).get('v', [''])[0]
        elif p.path.startswith('/shorts/') or p.path.startswith('/live/'):
            value = p.path.split('/')[2]
        else:
            raise ValueError('Use um link de video, Shorts ou live gravada.')
    elif host == 'youtu.be':
        value = p.path.strip('/')
    else:
        raise ValueError('Apenas links do YouTube sao aceitos.')
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', value):
        raise ValueError('ID de video invalido.')
    return value


def run(cmd, **kwargs):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, **kwargs)


def convert(url, width, height, fps, folder):
    source = folder / 'source.mp4'
    run([sys.executable, '-m', 'yt_dlp', '--no-playlist', '--no-progress', '--no-warnings',
         '--match-filter', f'duration <= {MAX_SECONDS} & !is_live',
         '--max-filesize', '150M', '-f', 'bv*[height<=480]+ba/b[height<=480]/b',
         '--merge-output-format', 'mp4', '-o', str(source), url], timeout=300)
    if not source.exists():
        raise RuntimeError('Nao foi possivel baixar o video. Veja disponibilidade e yt-dlp.')
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
    if proc.returncode != 0 or count == 0:
        raise RuntimeError('FFmpeg nao conseguiu converter o video.')
    audio = folder / 'audio.dfpwm'
    try:
        run(['ffmpeg', '-y', '-v', 'error', '-i', str(source), '-t', str(MAX_SECONDS),
             '-vn', '-ac', '1', '-ar', '48000', '-c:a', 'dfpwm', '-f', 'dfpwm', str(audio)], timeout=120)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        audio.unlink(missing_ok=True)
    source.unlink(missing_ok=True)
    return count, audio.exists(), ['#%02x%02x%02x' % rgb for rgb in palette]


def worker(generation, url, width, height, fps, folder):
    global job_dir
    try:
        count, has_audio, palette = convert(url, width, height, fps, folder)
        with lock:
            if job.get('generation') == generation:
                job.update(state='ready', frames=count, audio=has_audio, palette=palette)
    except Exception as ex:
        detail = str(ex)
        if isinstance(ex, subprocess.CalledProcessError) and ex.stderr:
            detail = ex.stderr.decode('utf-8', errors='replace')[-700:]
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
        global job_dir
        if self.path != '/start':
            return self.send(404, {'error': 'Rota inexistente'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 2048:
                raise ValueError('Pedido muito grande ou vazio.')
            req = json.loads(self.rfile.read(size))
            vid = video_id(req['url'])
            width, height = int(req['width']), int(req['height'])
            fps = int(req.get('fps', FPS))
            if not (4 <= width <= 240 and 4 <= height <= 100 and width * height <= MAX_PIXELS):
                raise ValueError('Resolucao invalida ou grande demais.')
            if not 1 <= fps <= 10:
                raise ValueError('FPS deve ser entre 1 e 10.')
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as ex:
            return self.send(400, {'error': str(ex)})
        with lock:
            if job['state'] == 'loading':
                return self.send(409, {'error': 'Ja existe uma conversao em andamento.'})
            old = job_dir
            folder = Path(tempfile.mkdtemp(prefix='cc_tv_'))
            job_dir = folder
            generation = job.get('generation', 0) + 1
            job.clear()
            job.update(state='loading', generation=generation, width=width, height=height, fps=fps)
        if old:
            shutil.rmtree(old, ignore_errors=True)
        url = 'https://www.youtube.com/watch?v=' + vid
        threading.Thread(target=worker, args=(generation, url, width, height, fps, folder), daemon=True).start()
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
            n = int(parse_qs(p.query).get('n', ['-1'])[0])
            if p.path == '/frame':
                if not 0 <= n < status['frames']:
                    raise ValueError('Quadro fora do intervalo.')
                with (folder / 'frames.bin').open('rb') as f:
                    f.seek(n * status['width'] * status['height'])
                    data = f.read(status['width'] * status['height'])
            else:
                if not status['audio'] or not 0 <= n <= 120 * 48000 // 8 // 6144 + 1:
                    raise ValueError('Audio indisponivel ou indice invalido.')
                with (folder / 'audio.dfpwm').open('rb') as f:
                    f.seek(n * 6144)
                    data = f.read(6144)
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
    print(f'Servidor iniciado em http://{args.host}:{args.port} (Ctrl+C para parar)', flush=True)
    print(f'CHAVE DE ACESSO: {ACCESS_TOKEN}', flush=True)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()

if __name__ == '__main__':
    main()
