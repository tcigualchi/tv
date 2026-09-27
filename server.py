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
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
import asyncio
import time
import uvicorn
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
         '--max-filesize', '300M', '-f', 'bv*[height<=480]+ba/b[height<=480]/b/bv*+ba/b/bestaudio/best',
         '--merge-output-format', 'mkv', '--print', 'before_dl:CC_TV_TITLE:%(title)s', '-o', str(folder / 'source.%(ext)s'), url]
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
    title = next((line.split('CC_TV_TITLE:', 1)[1].strip() for line in output.splitlines()
                  if line.startswith('CC_TV_TITLE:')), source.stem)[:120]
    if cancel.is_set():
        raise RuntimeError('Conversao cancelada.')
    probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type',
                            '-show_entries', 'format=duration', '-of', 'json', str(source)],
                           capture_output=True, text=True, check=True)
    media = json.loads(probe.stdout)
    audio_only = not any(s.get('codec_type') == 'video' for s in media.get('streams', []))
    if audio_only:
        if not any(s.get('codec_type') == 'audio' for s in media.get('streams', [])):
            raise RuntimeError('Arquivo sem video ou audio reproduzivel.')
        try:
            seconds = min(MAX_SECONDS, float(media['format']['duration']))
        except (KeyError, ValueError, TypeError):
            raise RuntimeError('Duracao da musica indisponivel.')
        if not 0 < seconds <= MAX_SECONDS:
            raise RuntimeError('Musica vazia ou acima do limite de 5 minutos.')
        palette = PALETTE
        count = min(fps * MAX_SECONDS, max(1, int(seconds * fps)))
        bg = b'f' * (width * height)
        with (folder / 'frames.bin').open('wb') as dst:
            for i in range(count):
                if cancel.is_set():
                    raise RuntimeError('Conversao cancelada.')
                frame = bytearray(bg)
                for bar in range(4, width - 4, max(2, width // 36)):
                    phase = (bar * 13 + i * 7) % 29
                    magnitude = max(2, (height // 2) * (8 + abs(phase - 14)) // 22)
                    for y in range(max(2, height // 2 - magnitude), min(height - 2, height // 2 + magnitude)):
                        frame[y * width + bar] = ord('b' if y < height // 2 else '9')
                dst.write(frame)
    else:
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
    return count, audio.exists(), ['#%02x%02x%02x' % rgb for rgb in palette], title


def worker(generation, url, width, height, fps, folder, cancel):
    global job_dir
    try:
        count, has_audio, palette, title = convert(url, width, height, fps, folder, cancel)
        with lock:
            if job.get('generation') == generation:
                job.update(state='ready', frames=count, audio=has_audio, palette=palette, title=title)
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


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
DASHBOARD = Path(__file__).with_name('dashboard.html')
SESSIONS = {}
SESSION_TTL = 24 * 3600
web_clients = set()
tv_socket = None
pending_add = {}
last_tv_state = {'type': 'state', 'online': False, 'playing': False}


def authenticated(request):
    bearer = request.headers.get('authorization', '')
    if hmac.compare_digest(bearer, 'Bearer ' + ACCESS_TOKEN):
        return True
    session = request.cookies.get('cc_tv_session', '')
    issued = SESSIONS.get(session, 0)
    return bool(session and issued and time.time() - issued < SESSION_TTL)


def require_auth(request):
    if not authenticated(request):
        raise HTTPException(401, detail='Chave de acesso incorreta.')


def browser_session(request):
    session = request.cookies.get('cc_tv_session', '')
    issued = SESSIONS.get(session, 0)
    return bool(session and issued and time.time() - issued < SESSION_TTL)


@app.get('/')
def home():
    return FileResponse(DASHBOARD, media_type='text/html', headers={'Cache-Control': 'no-store'})


@app.post('/api/login')
async def login(request: Request):
    data = await request.json()
    key = data.get('key', '') if isinstance(data, dict) else ''
    if not isinstance(key, str) or not hmac.compare_digest(key, ACCESS_TOKEN):
        raise HTTPException(401, detail='Chave incorreta.')
    session = secrets.token_urlsafe(32)
    SESSIONS[session] = time.time()
    response = Response(content='{"ok":true}', media_type='application/json')
    response.set_cookie('cc_tv_session', session, max_age=SESSION_TTL,
                        httponly=True, secure=True, samesite='strict')
    return response


@app.get('/api/session')
def session_status(request: Request):
    if not browser_session(request):
        raise HTTPException(401, detail='Sessao expirada.')
    return {'ok': True}


@app.get('/status')
def get_status(request: Request):
    require_auth(request)
    with lock:
        return {k: v for k, v in job.items() if k != 'generation'}


@app.post('/start')
async def start_video(request: Request):
    global job_dir, job_cancel
    require_auth(request)
    try:
        data = await request.json()
        video_url = validated_url(data['url'])
        width, height = int(data['width']), int(data['height'])
        fps = int(data.get('fps', FPS))
        if not (4 <= width <= 320 and 4 <= height <= 160 and width * height <= MAX_PIXELS):
            raise ValueError('Resolucao invalida ou grande demais.')
        if not 1 <= fps <= 10:
            raise ValueError('FPS deve ser entre 1 e 10.')
    except (ValueError, KeyError, TypeError) as ex:
        raise HTTPException(400, detail=str(ex)) from ex
    with lock:
        if job_cancel:
            job_cancel.set()
        old = job_dir if job['state'] != 'loading' else None
        folder = Path(tempfile.mkdtemp(prefix='cc_tv_'))
        job_dir = folder
        job_cancel = threading.Event()
        generation = job.get('generation', 0) + 1
        job.clear()
        job.update(state='loading', generation=generation, width=width, height=height, fps=fps)
        cancel = job_cancel
    if old:
        shutil.rmtree(old, ignore_errors=True)
    threading.Thread(target=worker,
                     args=(generation, video_url, width, height, fps, folder, cancel),
                     daemon=True).start()
    return Response(content='{"state":"loading"}', status_code=202, media_type='application/json')


@app.post('/cancel')
def cancel_video(request: Request):
    global job_dir
    require_auth(request)
    with lock:
        if job_cancel:
            job_cancel.set()
        old = job_dir if job['state'] == 'ready' else None
        job['generation'] = job.get('generation', 0) + 1
        job['state'] = 'idle'
        job_dir = None
    if old:
        shutil.rmtree(old, ignore_errors=True)
    return {'state': 'idle'}


@app.get('/frame')
def get_frame(request: Request, n: int, count: int = 1):
    require_auth(request)
    with lock:
        data, folder = job.copy(), job_dir
    if data['state'] != 'ready' or not folder:
        raise HTTPException(409, detail='Video ainda nao esta pronto.')
    if not 1 <= count <= 8 or count * data['width'] * data['height'] > 96000:
        raise HTTPException(400, detail='Lote de quadros grande demais.')
    if not 0 <= n < data['frames']:
        raise HTTPException(400, detail='Quadro fora do intervalo.')
    try:
        with (folder / 'frames.bin').open('rb') as f:
            f.seek(n * data['width'] * data['height'])
            payload = f.read(min(count, data['frames'] - n) * data['width'] * data['height'])
    except OSError as ex:
        raise HTTPException(409, detail='Video foi trocado.') from ex
    return Response(content=payload, media_type='application/octet-stream')


@app.get('/audio')
def get_audio(request: Request, n: int, count: int = 1):
    require_auth(request)
    with lock:
        data, folder = job.copy(), job_dir
    if data['state'] != 'ready' or not folder:
        raise HTTPException(409, detail='Audio ainda nao esta pronto.')
    if not data.get('audio') or not 1 <= count <= 8 or not 0 <= n <= MAX_SECONDS * 48000 // 8 // 6144 + 1:
        raise HTTPException(400, detail='Audio indisponivel ou indice invalido.')
    try:
        with (folder / 'audio.dfpwm').open('rb') as f:
            f.seek(n * 6144)
            payload = f.read(count * 6144)
    except OSError as ex:
        raise HTTPException(409, detail='Audio foi trocado.') from ex
    return Response(content=payload, media_type='application/octet-stream')


async def broadcast_state():
    for client in list(web_clients):
        try:
            await client.send_json(last_tv_state)
        except Exception:
            web_clients.discard(client)


@app.websocket('/ws')
async def websocket_endpoint(websocket: WebSocket):
    global tv_socket, last_tv_state
    origin = websocket.headers.get('origin')
    origin_host = urlparse(origin).hostname if origin else None
    request_host = (websocket.headers.get('x-forwarded-host') or websocket.headers.get('host') or '').split(':')[0]
    if origin and origin_host != request_host:
        await websocket.close(code=1008)
        return
    is_tv = hmac.compare_digest(websocket.headers.get('authorization', ''), 'Bearer ' + ACCESS_TOKEN)
    session = websocket.cookies.get('cc_tv_session', '')
    is_browser = bool(session and SESSIONS.get(session, 0) and time.time() - SESSIONS[session] < SESSION_TTL)
    if not (is_tv or is_browser):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    if is_tv:
        if tv_socket:
            try:
                await tv_socket.close(code=1000)
            except Exception:
                pass
        tv_socket = websocket
    else:
        web_clients.add(websocket)
        await websocket.send_json(last_tv_state)
    try:
        while True:
            data = await websocket.receive_json()
            if not isinstance(data, dict):
                continue
            if is_tv and data.get('type') == 'state':
                last_tv_state = {
                    'type': 'state', 'online': True,
                    'playing': bool(data.get('playing')),
                    'paused': bool(data.get('paused')),
                    'status': str(data.get('status', ''))[:100],
                    'title': str(data.get('title', ''))[:120],
                    'url': str(data.get('url', ''))[:1900],
                    'progress': max(0, min(300, float(data.get('progress') or 0))),
                    'duration': max(0, min(300, float(data.get('duration') or 0))),
                    'volume': max(0, min(3, float(data.get('volume') or 0))),
                }
                await broadcast_state()
            elif is_tv and data.get('type') == 'add_ack':
                item = pending_add.get(data.get('id'))
                if item and item[0] is websocket and not item[1].done():
                    item[1].set_result(bool(data.get('ok')))
            elif is_browser and data.get('type') == 'control':
                action = data.get('action')
                if action not in ('play', 'pause', 'resume', 'stop', 'next', 'volume', 'add_url'):
                    continue
                try:
                    value = max(0, min(3, float(data.get('value', 0)))) if action == 'volume' else None
                except (ValueError, TypeError):
                    continue
                if action == 'add_url':
                    try:
                        value = await asyncio.to_thread(validated_url, data.get('value'))
                    except ValueError as ex:
                        await websocket.send_json({'type': 'add_result', 'ok': False, 'message': str(ex)})
                        continue
                if tv_socket:
                    try:
                        if action == 'add_url':
                            target = tv_socket
                            request_id = secrets.token_hex(12)
                            acknowledged = asyncio.get_running_loop().create_future()
                            pending_add[request_id] = (target, acknowledged)
                            try:
                                await target.send_json({'type': 'control', 'action': action,
                                                        'value': value, 'id': request_id})
                                saved = await asyncio.wait_for(acknowledged, timeout=12)
                            except (asyncio.TimeoutError, WebSocketDisconnect, RuntimeError):
                                saved = False
                            finally:
                                pending_add.pop(request_id, None)
                            await websocket.send_json({'type': 'add_result', 'ok': saved,
                                'message': 'Adicionado à lista do Minecraft.' if saved else
                                'O Minecraft não confirmou o link. Reinicie tv.lua e tente novamente.'})
                        else:
                            await tv_socket.send_json({'type': 'control', 'action': action, 'value': value})
                    except Exception:
                        if action == 'add_url':
                            await websocket.send_json({'type': 'add_result', 'ok': False,
                                'message': 'Falha ao enviar para a TV. Tente novamente.'})
                elif action == 'add_url':
                    await websocket.send_json({'type': 'add_result', 'ok': False, 'message': 'Abra o programa tv no Minecraft.'})
    except (WebSocketDisconnect, RuntimeError, ValueError, TypeError):
        pass
    finally:
        if is_tv and tv_socket is websocket:
            tv_socket = None
            last_tv_state = {'type': 'state', 'online': False, 'playing': False}
            await broadcast_state()
        else:
            web_clients.discard(websocket)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=PORT)
    args = parser.parse_args()
    if importlib.util.find_spec('yt_dlp') is None:
        parser.error('Instale as dependencias com pip install -r requirements.txt')
    if not shutil.which('ffmpeg'):
        parser.error('ffmpeg nao encontrado no PATH')
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listener.bind((args.host, args.port))
        listener.listen(128)
    except OSError as ex:
        listener.close()
        parser.error(f'Nao foi possivel abrir a porta {args.port}: {ex}')
    print(f'Servidor iniciado em http://{args.host}:{args.port}', flush=True)
    print(f'CHAVE DE ACESSO: {ACCESS_TOKEN}', flush=True)
    config = uvicorn.Config(app, host=args.host, port=args.port, log_level='warning', ws='websockets')
    uvicorn.Server(config).run(sockets=[listener])

if __name__ == '__main__':
    main()
