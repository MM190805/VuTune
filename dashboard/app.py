"""
VuTune - IMVU Music Bot
Dashboard Flask App
"""

import asyncio
import threading
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from utils.config import save_config


def create_app(config: dict, room_manager, bot_loop: asyncio.AbstractEventLoop):
    app = Flask(__name__)
    app.config['SECRET_KEY'] = config['dashboard'].get('secret_key', 'dev-key')
    CORS(app)

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

    return app
