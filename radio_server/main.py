"""
VuTune Radio Server - Standalone
Runs as a separate Render service.
The bot POSTs audio chunks to /push, and IMVU listeners GET /stream.
Uses almost zero RAM (no browser, no Playwright).
"""

import os
import queue
import threading
import logging
from flask import Flask, Response, request, stream_with_context

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

# All connected IMVU listeners
_clients = set()
_clients_lock = threading.Lock()

# Shared secret so only our bot can push audio
PUSH_SECRET = os.environ.get("RADIO_PUSH_SECRET", "vutune-radio-secret")

# Load real silent MP3 — proper frames that any MP3 player can decode
# This prevents IMVU from getting stuck in "loading" state
try:
    with open(os.path.join(os.path.dirname(__file__), 'silence.mp3'), 'rb') as f:
        SILENCE = f.read()
    logger.info(f"Loaded silence.mp3 ({len(SILENCE)} bytes)")
except Exception:
    # Fallback minimal valid MP3 frame if file not found
    SILENCE = b'\xff\xfb\x90\x00' + b'\x00' * 413  # 417-byte silent MP3 frame
    SILENCE = SILENCE * 10
    logger.warning("silence.mp3 not found, using fallback silence bytes")



def broadcast(data: bytes):
    dead = set()
    with _clients_lock:
        clients = list(_clients)
    for q in clients:
        try:
            q.put_nowait(data)
        except queue.Full:
            dead.add(q)
        except Exception:
            dead.add(q)
    if dead:
        with _clients_lock:
            _clients.difference_update(dead)


# Background silence streamer — keeps IMVU connected when no music is playing
def silence_loop():
    import time
    while True:
        with _clients_lock:
            has_clients = len(_clients) > 0
        if has_clients:
            broadcast(SILENCE)
        time.sleep(0.5)

threading.Thread(target=silence_loop, daemon=True, name='SilenceLoop').start()


@app.route('/stream')
@app.route('/stream.mp3')
def radio_stream():
    """IMVU connects here to listen to the radio."""
    client_q = queue.Queue(maxsize=100)
    with _clients_lock:
        _clients.add(client_q)
    logger.info(f"New listener connected. Total: {len(_clients)}")

    def generate():
        try:
            # Send silence IMMEDIATELY so Render's proxy flushes headers right away
            yield SILENCE
            while True:
                try:
                    chunk = client_q.get(timeout=5)
                    yield chunk
                except queue.Empty:
                    # Keep connection alive with silence when no music is playing
                    yield SILENCE
        finally:
            with _clients_lock:
                _clients.discard(client_q)
            logger.info(f"Listener disconnected. Total: {len(_clients)}")

    return Response(
        stream_with_context(generate()),
        mimetype='audio/mpeg',
        headers={
            'icy-name': 'VuTune Radio',
            'icy-br': '128',
            'icy-genre': 'Various',
            'icy-pub': '1',
            'Cache-Control': 'no-cache, no-store',
            'Connection': 'keep-alive',
            'Transfer-Encoding': 'chunked',
            'Access-Control-Allow-Origin': '*',
            'X-Accel-Buffering': 'no',   # Disables Nginx/Render proxy buffering
            'X-Content-Type-Options': 'nosniff',
        }
    )



@app.route('/push', methods=['POST'])
def push_audio():
    """The bot POSTs audio chunks here while a song is playing."""
    secret = request.headers.get('X-Radio-Secret', '')
    if secret != PUSH_SECRET:
        return 'Forbidden', 403
    data = request.get_data()
    if data:
        broadcast(data)
    return 'OK', 200


@app.route('/ping')
def ping():
    return f'VuTune Radio - {len(_clients)} listeners', 200


@app.route('/')
def index():
    return f"""
    <html><body style="background:#111;color:white;font-family:sans-serif;text-align:center;padding:40px;">
    <h1>🎵 VuTune Radio</h1>
    <p>Stream URL: <code>{request.host_url}stream</code></p>
    <p>Active listeners: {len(_clients)}</p>
    <audio controls autoplay src="/stream" style="width:400px;margin-top:20px;"></audio>
    </body></html>
    """, 200


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    logger.info(f"VuTune Radio Server starting on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
