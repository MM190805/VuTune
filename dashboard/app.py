"""
VuTune - IMVU Music Bot
Dashboard Flask App
"""

import asyncio
import threading
import time
import os
import logging
from flask import Flask, render_template, request, jsonify, Response, stream_with_context
from flask_cors import CORS
from utils.config import save_config

logger = logging.getLogger(__name__)

# Shared audio buffer for radio streaming
_audio_clients = set()
_audio_clients_lock = threading.Lock()
_current_audio_chunk = bytearray()
_audio_lock = threading.Lock()

def broadcast_audio(data: bytes):
    """Called by the player to push audio to all connected radio listeners."""
    global _current_audio_chunk
    with _audio_lock:
        _current_audio_chunk = bytearray(data)
    dead = set()
    with _audio_clients_lock:
        clients = list(_audio_clients)
    
    import queue
    for q in clients:
        try:
            q.put_nowait(data)
        except queue.Full:
            # Buffer is full! Drop the oldest audio chunk to make room.
            # Do NOT kill the client, otherwise they hear silence forever!
            try:
                q.get_nowait()
                q.put_nowait(data)
            except Exception:
                pass
        except Exception:
            dead.add(q)
    if dead:
        with _audio_clients_lock:
            _audio_clients.difference_update(dead)


def create_app(config: dict, room_manager, bot_loop: asyncio.AbstractEventLoop):
    app = Flask(__name__)
    app.config['SECRET_KEY'] = config['dashboard'].get('secret_key', 'dev-key')
    CORS(app)

    # ------------------------------------------------------------------ #
    #  Self-ping uptime keeper (prevents Render from spinning down)        #
    # ------------------------------------------------------------------ #
    def _self_ping():
        import urllib.request
        while True:
            time.sleep(270)  # ping every 4.5 minutes
            try:
                render_url = os.environ.get('RENDER_EXTERNAL_URL', '')
                if render_url:
                    urllib.request.urlopen(f'{render_url}/ping', timeout=10)
                    logger.info("Self-ping sent to keep Render alive.")
            except Exception as e:
                logger.warning(f"Self-ping failed (OK if first startup): {e}")

    ping_thread = threading.Thread(target=_self_ping, daemon=True, name='UptimePinger')
    ping_thread.start()

    # ------------------------------------------------------------------ #
    #  Pages                                                               #
    # ------------------------------------------------------------------ #

    @app.route('/')
    def index():
        return render_template('index.html')

    # ------------------------------------------------------------------ #
    #  Status API                                                          #
    # ------------------------------------------------------------------ #

    @app.route('/api/status')
    def api_status():
        return jsonify(room_manager.get_status())

    # ------------------------------------------------------------------ #
    #  Room Management                                                     #
    # ------------------------------------------------------------------ #

    @app.route('/api/rooms', methods=['POST'])
    def api_add_room():
        data = request.get_json(force=True)
        room_id = str(data.get('room_id', '')).strip()
        room_name = str(data.get('room_name', f'Room {room_id}')).strip()

        if not room_id:
            return jsonify({'success': False, 'error': 'room_id required'}), 400

        success = room_manager.add_room(room_id, room_name)
        if success:
            # Persist to config
            config['rooms'].append({'id': room_id, 'name': room_name})
            save_config(config)

        return jsonify({'success': success})

    @app.route('/api/rooms/<room_id>', methods=['DELETE'])
    def api_remove_room(room_id):
        success = room_manager.remove_room(room_id)
        if success:
            config['rooms'] = [r for r in config['rooms'] if r['id'] != room_id]
            save_config(config)
        return jsonify({'success': success})

    # ------------------------------------------------------------------ #
    #  Music Controls                                                      #
    # ------------------------------------------------------------------ #

    @app.route('/api/play', methods=['POST'])
    def api_play():
        data = request.get_json(force=True)
        query = str(data.get('query', '')).strip()
        if not query:
            return jsonify({'success': False, 'error': 'query required'}), 400

        def _do_play():
            song = room_manager.player.search_youtube(query)
            if song:
                room_manager.player.play(song)
                msg = f"🎵 Now Playing: {song['title']}"
                for rid in list(room_manager.rooms.keys()):
                    room_manager.imvu.send_message(rid, msg)

        threading.Thread(target=_do_play, daemon=True).start()
        return jsonify({'success': True, 'message': f'Searching for: {query}'})

    @app.route('/api/add', methods=['POST'])
    def api_add():
        data = request.get_json(force=True)
        query = str(data.get('query', '')).strip()
        if not query:
            return jsonify({'success': False, 'error': 'query required'}), 400

        def _do_add():
            song = room_manager.player.search_youtube(query)
            if song:
                room_manager.queue.add(song)
                pos = room_manager.queue.size()
                msg = f"✅ Added to queue #{pos}: {song['title']}"
                for rid in list(room_manager.rooms.keys()):
                    room_manager.imvu.send_message(rid, msg)

        threading.Thread(target=_do_add, daemon=True).start()
        return jsonify({'success': True, 'message': f'Adding: {query}'})

    @app.route('/api/skip', methods=['POST'])
    def api_skip():
        nxt = room_manager.queue.next()
        if nxt:
            room_manager.player.play(nxt)
            return jsonify({'success': True, 'now_playing': nxt['title']})
        room_manager.player.stop()
        return jsonify({'success': True, 'now_playing': None})

    @app.route('/api/stop', methods=['POST'])
    def api_stop():
        room_manager.player.stop()
        return jsonify({'success': True})

    @app.route('/api/queue', methods=['DELETE'])
    def api_clear_queue():
        room_manager.queue.clear()
        return jsonify({'success': True})

    @app.route('/api/queue/<int:index>', methods=['DELETE'])
    def api_remove_from_queue(index):
        removed = room_manager.queue.remove(index)
        if removed:
            return jsonify({'success': True, 'removed': removed['title']})
        return jsonify({'success': False, 'error': 'Invalid index'}), 400

    # ------------------------------------------------------------------ #
    #  Settings                                                            #
    # ------------------------------------------------------------------ #

    @app.route('/api/settings', methods=['GET'])
    def api_get_settings():
        return jsonify({
            'anyone_can_use': config.get('anyone_can_use', True),
            'allowed_users': config.get('allowed_users', []),
            'bot_name': config.get('bot_name', 'VuTune'),
            'icecast_mount': config['icecast']['mount'],
            'icecast_port': config['icecast']['port'],
        })

    @app.route('/api/settings', methods=['POST'])
    def api_save_settings():
        data = request.get_json(force=True)
        if 'anyone_can_use' in data:
            config['anyone_can_use'] = bool(data['anyone_can_use'])
            room_manager.cmd_handler.anyone_can_use = config['anyone_can_use']
        if 'allowed_users' in data:
            config['allowed_users'] = data['allowed_users']
            room_manager.cmd_handler.allowed_users = [
                u.lower() for u in data['allowed_users']
            ]
        save_config(config)
        return jsonify({'success': True})

    # ------------------------------------------------------------------ #
    #  Radio Stream (serves audio directly from bot's player)              #
    # ------------------------------------------------------------------ #

    @app.route('/stream')
    @app.route('/stream.mp3')
    def radio_stream():
        import queue as queue_module
        client_q = queue_module.Queue(maxsize=300)
        with _audio_clients_lock:
            _audio_clients.add(client_q)

        def generate():
            # Load real silent MP3 to prevent strict decoders from crashing
            try:
                import os
                silence_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'radio_server', 'silence.mp3')
                with open(silence_path, 'rb') as f:
                    silence_data = f.read()
                # Strip ID3 tags if present to prevent strict decoders from rejecting the stream
                sync_idx = silence_data.find(b'\xff\xfb')
                if sync_idx != -1:
                    silence_data = silence_data[sync_idx:]
                # Extract exactly ONE 417-byte frame and multiply it to make a perfect 2-second payload
                # This guarantees mathematically perfect frame boundaries so the decoder never crashes
                silence_payload = silence_data[:417] * 76
            except Exception:
                # Absolute fallback (will likely crash strict decoders, but better than nothing)
                silence_payload = (b'\xff\xfb\x90\x00' + (b'\x00' * 413)) * 76
                
            try:
                # Send the entire 2-second silence payload IMMEDIATELY to prevent IMVU from timing out
                yield silence_payload
                while True:
                    try:
                        chunk = client_q.get(timeout=2)
                        yield chunk
                    except Exception:
                        # Send a full 2-seconds of silence to maintain the 128kbps bitrate!
                        # If we send too little data, the player starves and disconnects after ~1 min.
                        yield silence_payload
            finally:
                with _audio_clients_lock:
                    _audio_clients.discard(client_q)

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
                'Access-Control-Allow-Origin': '*',
                'X-Accel-Buffering': 'no',
                'X-Content-Type-Options': 'nosniff',
            }
        )

    @app.route('/ping')
    def ping():
        return 'pong', 200

    # ------------------------------------------------------------------ #
    #  Debug / 2FA / Logs (unified from minicast_async.py)                 #
    # ------------------------------------------------------------------ #

    @app.route('/debug')
    def debug_page():
        import base64
        img_src = ''
        try:
            with open('debug.jpg', 'rb') as f:
                img_src = 'data:image/jpeg;base64,' + base64.b64encode(f.read()).decode()
        except Exception:
            pass
        html = f"""
        <html><body style="background:#111;color:white;font-family:sans-serif;text-align:center;">
        <h2>VuTune Live Bot Camera</h2>
        {'<img src="' + img_src + '" style="max-width:80%;border:2px solid #444;border-radius:8px;"/><br><br>' if img_src else '<p>No screenshot yet...</p>'}
        <h3>Submit 2FA code if you see a prompt above:</h3>
        <form method="POST" action="/debug/2fa">
            <input type="text" name="code" placeholder="Enter 2FA Code" style="padding:10px;font-size:16px;" required/>
            <button type="submit" style="padding:10px 20px;font-size:16px;background:#e6a715;border:none;border-radius:4px;font-weight:bold;cursor:pointer;">Submit</button>
        </form><br>
        <button onclick="location.reload()" style="padding:10px;">Refresh Camera</button>
        </body></html>
        """
        return html, 200, {'Content-Type': 'text/html'}

    @app.route('/debug/2fa', methods=['POST'])
    def debug_2fa():
        code = request.form.get('code', '').strip()
        if code:
            # Write to file so the bot's async thread can pick it up
            with open('2fa_code.txt', 'w') as f:
                f.write(code)
            # Also signal directly if the browser client is available
            try:
                room_manager.imvu.provide_2fa(code)
            except Exception:
                pass
            return '<h2>2FA Submitted! <a href="/debug" style="color:#e6a715;">Go back</a></h2>', 200, {'Content-Type': 'text/html'}
        return '<h2>No code provided.</h2>', 400, {'Content-Type': 'text/html'}

    @app.route('/logs')
    def logs():
        try:
            if os.path.exists('vutune.log'):
                with open('vutune.log', 'r', encoding='utf-8', errors='replace') as f:
                    lines = f.readlines()
                return ''.join(lines[-150:]), 200, {'Content-Type': 'text/plain'}
            return 'No vutune.log found.', 200, {'Content-Type': 'text/plain'}
        except Exception as e:
            return str(e), 500

    return app
