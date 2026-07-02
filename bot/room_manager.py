"""
VuTune - IMVU Music Bot
Room Manager

Manages all room bots and shared resources (player, queue).
One player/queue shared across all rooms.
"""

import asyncio
import logging
import json
import os

from .imvu_browser import IMVUBrowserClient
from .player import MusicPlayer
from .queue_manager import QueueManager
from .command_handler import CommandHandler

logger = logging.getLogger(__name__)

class RoomBot:
    """Bot instance managing a single IMVU room via Playwright browser."""

    def __init__(self, room_id: str, room_name: str,
                 imvu: IMVUBrowserClient, cmd_handler: CommandHandler):
        self.room_id = room_id
        self.room_name = room_name
        self.imvu = imvu
        self.cmd_handler = cmd_handler
        self.is_active = False
        self._task = None

    async def run(self):
        """Main poll loop for this room."""
        self.is_active = True
        
        async def on_message(username, text):
            response = self.cmd_handler.handle(self.room_id, username, text)
            if response:
                await self.imvu.send_message(self.room_id, response)
                
        success = await self.imvu.join_room(self.room_id, on_message)
        if not success:
            logger.error(f"Could not join room {self.room_id}")
            self.is_active = False
            return

        logger.info(f"VuTune active in room '{self.room_name}' ({self.room_id})")

        while self.is_active:
            await asyncio.sleep(1)

    async def stop(self):
        self.is_active = False
        await self.imvu.leave_room(self.room_id)
        if self._task:
            self._task.cancel()


class RoomManager:
    """Top-level manager owning the IMVU browser client, player, queue, and all RoomBots."""

    def __init__(self, config: dict, loop: asyncio.AbstractEventLoop):
        self.config = config
        self.loop = loop

        # Load session data
        session_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'session.json')
        session_data = {}
        if os.path.exists(session_path):
            with open(session_path, 'r') as f:
                session_data = json.load(f)

        self.imvu = IMVUBrowserClient(session_data, credentials=config.get('imvu', {}))
        self.player = MusicPlayer(config['icecast'])
        self.queue = QueueManager()
        self.cmd_handler = CommandHandler(
            player=self.player,
            queue=self.queue,
            imvu_client=self.imvu,
            loop=self.loop,
            allowed_users=config.get('allowed_users', []),
            anyone_can_use=config.get('anyone_can_use', True),
            dashboard_url=f"http://localhost:{config['dashboard']['port']}",
            icecast_config=config['icecast'],
        )
        self.rooms = {}   # room_id -> RoomBot
        self.player.on_song_end = self._on_song_end

    def _on_song_end(self):
        nxt = self.queue.next()
        if nxt:
            self.player.play(nxt)

    async def start(self):
        """Start Playwright and join any pre-configured rooms."""
        logger.info("Starting VuTune Browser Client...")
        await self.imvu.start()

        for room_cfg in self.config.get('rooms', []):
            await self._start_room(room_cfg['id'], room_cfg.get('name', room_cfg['id']))

        while True:
            await asyncio.sleep(1)

    async def _start_room(self, room_id: str, room_name: str):
        if room_id in self.rooms:
            return
        bot = RoomBot(room_id, room_name, self.imvu, self.cmd_handler)
        self.rooms[room_id] = bot
        task = asyncio.ensure_future(bot.run())
        bot._task = task

    # --- Dashboard API (called from Flask threads) ---

    def add_room(self, room_id: str, room_name: str) -> bool:
        if room_id in self.rooms:
            return False
        future = asyncio.run_coroutine_threadsafe(
            self._start_room(room_id, room_name), self.loop
        )
        future.result(timeout=15)
        return True

    def remove_room(self, room_id: str) -> bool:
        if room_id not in self.rooms:
            return False
        bot = self.rooms[room_id]
        asyncio.run_coroutine_threadsafe(bot.stop(), self.loop)
        del self.rooms[room_id]
        return True

    def get_status(self) -> dict:
        return {
            'logged_in': self.imvu.is_logged_in,
            'bot_username': getattr(self.imvu, 'username', 'VuTune'),
            'is_playing': self.player.is_playing,
            'current_song': self.player.current_song,
            'queue': [
                {
                    'title': s['title'],
                    'duration': s.get('duration', 0),
                    'uploader': s.get('uploader', ''),
                    'thumbnail': s.get('thumbnail', ''),
                }
                for s in self.queue.list()
            ],
            'queue_size': self.queue.size(),
            'rooms': [
                {'id': rid, 'name': bot.room_name, 'active': bot.is_active}
                for rid, bot in self.rooms.items()
            ],
        }
