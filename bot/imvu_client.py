"""
VuTune - IMVU Music Bot
IMVU API Client

Handles login, room joining, chat polling, and sending messages.
Uses IMVU's HTTP API with session cookies.
"""

import requests
import logging
import json
import os
import time

SESSION_FILE = os.path.join(os.path.dirname(__file__), '..', 'session.json')

logger = logging.getLogger(__name__)


class IMVUClient:
    """
    IMVU API client for bot functionality.
    Logs in as a regular IMVU user account and automates
    room joining and chat interaction.
    """

    BASE_URL = "https://api.imvu.com"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'Origin': 'https://www.imvu.com',
            'Referer': 'https://www.imvu.com/',
        })
        self.user_id = None
        self.username = None
        self.is_logged_in = False
        self._last_message_ids = {}   # room_id -> last seen message id

        # Try to restore a previously saved session
        self._load_session()

    def _load_session(self):
        """Load saved session cookies from imvu_setup.py run."""
        path = os.path.abspath(SESSION_FILE)
        if not os.path.exists(path):
            return
        try:
            with open(path) as f:
                data = json.load(f)
            for name, value in data.get('cookies', {}).items():
                self.session.cookies.set(name, value)
            self.user_id = data.get('user_id', '')
            self.username = data.get('username', '')
            self.is_logged_in = True
            logger.info(f"Restored saved IMVU session for '{self.username}'")
        except Exception as e:
            logger.warning(f"Could not load session.json: {e}")

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def login(self, username: str, password: str) -> bool:
        """Login to IMVU. Uses saved session if available, otherwise fresh login."""
        # If session was already restored from file, skip fresh login
        if self.is_logged_in:
            logger.info(f"Using saved session for '{self.username}' (no login needed)")
            return True
        # Try different payload formats — IMVU's API varies
        payloads = [
            {"username": username, "password": password},
            {"avatarname": username, "password": password},
            {"username": username, "password": password, "remember_me": True},
        ]

        for payload in payloads:
            try:
                resp = self.session.post(
                    f"{self.BASE_URL}/login",
                    json=payload,
                    timeout=20,
                )

                if resp.status_code == 200:
                    data = resp.json()
                    # Try denormalized format
                    denormalized = data.get('denormalized', {})
                    for key in denormalized:
                        if key.startswith('user-'):
                            self.user_id = key.replace('user-', '')
                            self.username = username
                            self.is_logged_in = True
                            logger.info(f"Logged into IMVU as '{username}' (id={self.user_id})")
                            return True
                    # Try 'data' key format
                    if 'data' in data:
                        uid = data['data'].get('id', '')
                        self.user_id = str(uid).replace('user-', '')
                        self.username = username
                        self.is_logged_in = True
                        logger.info(f"Logged into IMVU as '{username}'")
                        return True
                    # Try top-level id
                    if data.get('id'):
                        self.user_id = str(data['id']).replace('user-', '')
                        self.username = username
                        self.is_logged_in = True
                        logger.info(f"Logged into IMVU as '{username}'")
                        return True

                logger.debug(f"Login attempt [{resp.status_code}]: {resp.text[:200]}")

            except requests.RequestException as e:
                logger.error(f"IMVU login request error: {e}")

        # All attempts failed — run in dashboard-only mode so UI still works
        logger.warning("IMVU login failed — bot running in dashboard-only mode.")
        logger.warning("Dashboard and music controls still work. Fix credentials in config.json.")
        self.username = username
        self.is_logged_in = False
        return False

    # ------------------------------------------------------------------
    # Room management
    # ------------------------------------------------------------------

    def join_room(self, room_id: str) -> bool:
        """Join an IMVU room as the bot avatar."""
        try:
            resp = self.session.post(
                f"{self.BASE_URL}/room/room-{room_id}/avatars",
                json={"id": f"user-{self.user_id}"},
                timeout=15,
            )
            if resp.status_code in (200, 201):
                self._last_message_ids[room_id] = None
                logger.info(f"✅ Joined room {room_id}")
                return True

            logger.warning(f"Could not join room {room_id} [{resp.status_code}]: {resp.text[:200]}")
            return False

        except requests.RequestException as e:
            logger.error(f"join_room error: {e}")
            return False

    def leave_room(self, room_id: str) -> bool:
        """Leave an IMVU room."""
        try:
            resp = self.session.delete(
                f"{self.BASE_URL}/room/room-{room_id}/avatars/user-{self.user_id}",
                timeout=10,
            )
            self._last_message_ids.pop(room_id, None)
            return resp.status_code in (200, 204)
        except requests.RequestException as e:
            logger.error(f"leave_room error: {e}")
            return False

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------

    def get_new_messages(self, room_id: str) -> list:
        """
        Poll for new chat messages in a room.
        Returns a list of message dicts: {username, message, id}
        """
        try:
            params = {'limit': 30}
            last_id = self._last_message_ids.get(room_id)
            if last_id:
                params['after_id'] = last_id

            resp = self.session.get(
                f"{self.BASE_URL}/room/room-{room_id}/chats",
                params=params,
                timeout=10,
            )

            if resp.status_code == 200:
                data = resp.json()
                items = data.get('data', {}).get('items', [])

                if items:
                    self._last_message_ids[room_id] = items[-1].get('id')

                # Normalise message format
                messages = []
                for item in items:
                    messages.append({
                        'id': item.get('id'),
                        'username': item.get('user', {}).get('name', ''),
                        'message': item.get('message', ''),
                        'timestamp': item.get('created_at', ''),
                    })
                return messages

            return []

        except requests.RequestException as e:
            logger.error(f"get_new_messages error: {e}")
            return []

    def send_message(self, room_id: str, text: str) -> bool:
        """Send a chat message to an IMVU room."""
        try:
            resp = self.session.post(
                f"{self.BASE_URL}/room/room-{room_id}/chats",
                json={"message": text, "type": "text"},
                timeout=10,
            )
            return resp.status_code in (200, 201)
        except requests.RequestException as e:
            logger.error(f"send_message error: {e}")
            return False
