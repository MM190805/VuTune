"""
VuTune - IMVU Music Bot
Command Handler — Full command set

!web            - Get the web player URL
!radio          - Get the radio stream URL for rooms
!play [song]    - Play a song (search YouTube)
!add [song]     - Add song to playlist
!next           - Skip to next song
!prev           - Play previous song
!stop           - Stop the radio
!remove [song]  - Remove a song from playlist (optional name, else removes current)
!clear          - Clear the playlist
!playlist       - Show next 10 songs in playlist
!playing        - Now playing
!listeners      - View listener count
!player         - Get the classic web player link
!mhere          - Move the NPC/bot to this room
!silent         - Toggle mute automatic messages
!showwhoadded   - Show who added each song
!control        - Show bot control info
!install        - How to install radio in your room
!mban [user]    - Ban a user from using bot
!munban [user]  - Unban a user
!fillplaylist   - AI-generate playlist suggestions
!help           - List all commands
"""

import logging
import threading

logger = logging.getLogger(__name__)


class CommandHandler:
    def __init__(self, player, queue, imvu_client, loop,
                 allowed_users=None, anyone_can_use=True,
                 dashboard_url="http://localhost:5000",
                 icecast_config=None):
        self.player = player
        self.queue = queue
        self.imvu = imvu_client
        self.loop = loop
        self.allowed_users = [u.lower() for u in (allowed_users or [])]
        self.anyone_can_use = anyone_can_use
        self.dashboard_url = dashboard_url
        self.icecast = icecast_config or {}
        self._search_lock = threading.Lock()
        self._banned = set()          # banned usernames (lowercase)
        self._silent = False           # if True, suppress auto messages
        self._show_who_added = False   # show who added each song
        self._history = []             # last 20 played songs (for !prev)
        self._who_added = {}           # title -> username

    # ------------------------------------------------------------------

    def stream_url(self):
        import os
        render_url = os.environ.get('RENDER_EXTERNAL_URL')
        if render_url:
            return f"{render_url}/stream"
        
        if 'public_url' in self.icecast_config:
            return self.icecast_config['public_url']
        host = self.icecast_config.get('host', 'localhost')
        port = self.icecast_config.get('port', 8000)
        mount = self.icecast_config.get('mount', '/stream')
        return f"http://{host}:{port}{mount}"

    def _can_use(self, username: str) -> bool:
        if username.lower() in self._banned:
            return False
        if self.anyone_can_use:
            return True
        return username.lower() in self.allowed_users

    def _is_admin(self, username: str) -> bool:
        return username.lower() in self.allowed_users

    def handle(self, room_id: str, username: str, message: str):
        """
        Parse a chat message and return a response string or None.
        Background search commands run in threads so the poll loop never blocks.
        """
        msg = message.strip()
        if not msg.startswith('!'):
            return None

        parts = msg.split(None, 1)
        cmd = parts[0].lower()
        args = parts[1].strip() if len(parts) > 1 else ''

        # Admin-only commands
        admin_cmds = {'!mban', '!munban', '!silent', '!control'}
        if cmd in admin_cmds and not self._is_admin(username):
            return None

        # Ban check (after admin-only gate)
        if not self._can_use(username):
            return None

        # Background search commands
        if cmd in ('!play', '!add'):
            if not args:
                return f"❌ Usage: {cmd} [song name]"
            threading.Thread(
                target=self._search_and_act,
                args=(room_id, cmd, args, username),
                daemon=True,
            ).start()
            return f"🔍 Searching: {args}..."

        if cmd == '!fillplaylist':
            threading.Thread(
                target=self._fill_playlist,
                args=(room_id, args or 'popular mix'),
                daemon=True,
            ).start()
            return "🤖 Generating AI playlist..."

        # Instant commands
        dispatch = {
            '!next':          self._cmd_next,
            '!skip':          self._cmd_next,
            '!prev':          self._cmd_prev,
            '!stop':          self._cmd_stop,
            '!clear':         self._cmd_clear,
            '!playlist':      self._cmd_playlist,
            '!playing':       self._cmd_playing,
            '!np':            self._cmd_playing,
            '!listeners':     self._cmd_listeners,
            '!web':           self._cmd_web,
            '!player':        self._cmd_web,
            '!radio':         self._cmd_radio,
            '!install':       self._cmd_install,
            '!showwhoadded':  self._cmd_showwhoadded,
            '!control':       self._cmd_control,
            '!help':          self._cmd_help,
        }

        if cmd == '!remove':
            return self._cmd_remove(args)
        if cmd == '!mban':
            return self._cmd_mban(args)
        if cmd == '!munban':
            return self._cmd_munban(args)
        if cmd == '!silent':
            return self._cmd_silent()
        if cmd == '!mhere':
            return self._cmd_mhere(room_id)

        if cmd in dispatch:
            return dispatch[cmd]()

        return None

    # ------------------------------------------------------------------
    # Background helpers
    # ------------------------------------------------------------------

    def _send_msg(self, room_id, msg):
        import asyncio
        asyncio.run_coroutine_threadsafe(self.imvu.send_message(room_id, msg), self.loop)

    def _search_and_act(self, room_id, cmd, query, username):
        with self._search_lock:
            song = self.player.search_youtube(query)

        if not song:
            self._send_msg(room_id, f"❌ Could not find: {query}")
            return

        if self._show_who_added:
            self._who_added[song['title']] = username

        if cmd == '!play':
            # Save current to history before changing
            if self.player.current_song:
                self._history.append(self.player.current_song)
                if len(self._history) > 20:
                    self._history.pop(0)
            self.player.play(song)
            msg = f"🎵 Now Playing: {song['title']}"
            if self._show_who_added:
                msg += f" (added by {username})"
            self._send_msg(room_id, msg)

        elif cmd == '!add':
            self.queue.add(song)
            pos = self.queue.size()
            msg = f"✅ Added #{pos}: {song['title']}"
            if self._show_who_added:
                msg += f" (by {username})"
            self._send_msg(room_id, msg)

    def _fill_playlist(self, room_id, genre):
        """Generate a basic AI-suggested playlist based on genre/mood."""
        suggestions = {
            'hindi': ['Tum Hi Ho Aashiqui 2', 'Kesariya Brahmastra', 'Raataan Lambiyan',
                      'Tera Yaar Hoon Main', 'Shayad Love Aaj Kal'],
            'english': ['Flowers Miley Cyrus', 'As It Was Harry Styles',
                        'Blinding Lights The Weeknd', 'Stay Kid LAROI',
                        'Love Story Taylor Swift'],
            'arabic': ['Maak Nassem', 'Ya Tabtab Mohamed Mounir',
                       'Enta Omri Umm Kulthum', 'Habibi Nancy Ajram'],
            'pop': ['Popular The Weeknd', 'Anti-Hero Taylor Swift',
                    'Dance The Night Dua Lipa', 'Escapism RAYE'],
        }
        genre_lower = genre.lower()
        songs_to_add = []
        for key, vals in suggestions.items():
            if key in genre_lower:
                songs_to_add = vals
                break
        if not songs_to_add:
            songs_to_add = suggestions['pop']  # default

        added = 0
        for query in songs_to_add:
            song = self.player.search_youtube(query)
            if song:
                self.queue.add(song)
                added += 1

        self._send_msg(room_id, f"🤖 Added {added} songs to the playlist!")

    # ------------------------------------------------------------------
    # Instant command implementations
    # ------------------------------------------------------------------

    def _cmd_next(self):
        if self.player.current_song:
            self._history.append(self.player.current_song)
            if len(self._history) > 20:
                self._history.pop(0)
        nxt = self.queue.next()
        if nxt:
            self.player.play(nxt)
            return f"⏭️ Next: {nxt['title']}"
        self.player.stop()
        return "⏭️ Queue is empty. Music stopped."

    def _cmd_prev(self):
        if not self._history:
            return "⏮️ No previous songs in history."
        prev = self._history.pop()
        # Put current back at front of queue
        if self.player.current_song:
            songs = self.queue.list()
            self.queue.clear()
            self.queue.add(self.player.current_song)
            for s in songs:
                self.queue.add(s)
        self.player.play(prev)
        return f"⏮️ Playing previous: {prev['title']}"

    def _cmd_stop(self):
        self.player.stop()
        return "⏹️ Radio stopped."

    def _cmd_clear(self):
        self.queue.clear()
        return "🗑️ Playlist cleared!"

    def _js_get_messages(self):
        return """
        () => {
            let msgs = [];
            document.querySelectorAll('.user-text').forEach(el => {
                el.innerText.trim().split('\\n').forEach(line => {
                    let text = line.trim();
                    if (text) msgs.push(text);
                });
            });
            return msgs;
        }
        """

    def _cmd_remove(self, args):
        if not args:
            # Remove current song — stop it, play next
            if self.player.current_song:
                title = self.player.current_song['title']
                nxt = self.queue.next()
                if nxt:
                    self.player.play(nxt)
                else:
                    self.player.stop()
                return f"🗑️ Removed current: {title}"
            return "⏸️ Nothing is playing."

        # Try to remove by number
        try:
            index = int(args)
            removed = self.queue.remove(index)
            if removed:
                return f"🗑️ Removed #{index}: {removed['title']}"
            return f"❌ No song at position {index}."
        except ValueError:
            # Try by title substring
            songs = self.queue.list()
            for i, s in enumerate(songs, 1):
                if args.lower() in s['title'].lower():
                    self.queue.remove(i)
                    return f"🗑️ Removed: {s['title']}"
            return f"❌ Song not found: {args}"

    def _cmd_playlist(self):
        songs = self.queue.list()
        if not songs:
            return "📭 Playlist is empty."
        lines = ["📋 Next 10 songs:"]
        for i, s in enumerate(songs[:10], 1):
            added_by = self._who_added.get(s['title'], '')
            line = f"  {i}. {s['title']}"
            if self._show_who_added and added_by:
                line += f" [{added_by}]"
            lines.append(line)
        if len(songs) > 10:
            lines.append(f"  ... +{len(songs)-10} more")
        return "\n".join(lines)

    def _cmd_playing(self):
        s = self.player.current_song
        if s:
            msg = f"🎵 Now Playing: {s['title']}"
            added_by = self._who_added.get(s['title'], '')
            if self._show_who_added and added_by:
                msg += f" | Added by: {added_by}"
            return msg
        return "⏸️ Nothing is playing."

    def _cmd_listeners(self):
        # Icecast admin API to get listener count
        try:
            import requests
            host = self.icecast.get('host', 'localhost')
            port = self.icecast.get('port', 8000)
            mount = self.icecast.get('mount', '/stream')
            admin_pass = self.icecast.get('admin_password', 'vutune_admin')
            r = requests.get(
                f"http://{host}:{port}/status-json.xsl",
                auth=('admin', admin_pass), timeout=5
            )
            if r.ok:
                data = r.json()
                sources = data.get('icestats', {}).get('source', [])
                if isinstance(sources, dict):
                    sources = [sources]
                for src in sources:
                    if src.get('mount', '').replace('/', '') == mount.replace('/', ''):
                        count = src.get('listeners', 0)
                        return f"🎧 Listeners: {count}"
        except Exception:
            pass
        return "🎧 Listener count unavailable (Icecast may not be running)."

    def _cmd_web(self):
        return f"🌐 VuTune Dashboard: {self.dashboard_url}"

    def _cmd_radio(self):
        url = self.stream_url()
        return f"📻 Radio URL: {url}\n(Paste this into your IMVU room's radio/media settings)"

    def _cmd_install(self):
        url = self.stream_url()
        return (
            "📡 How to install VuTune radio in your room:\n"
            f"  1. Copy this URL: {url}\n"
            "  2. Open your IMVU room settings\n"
            "  3. Go to Media / Radio settings\n"
            "  4. Paste the URL and save\n"
            "  Music will play automatically for everyone! 🎶"
        )

    def _cmd_showwhoadded(self):
        self._show_who_added = not self._show_who_added
        state = "ON" if self._show_who_added else "OFF"
        return f"👤 Show who added: {state}"

    def _cmd_control(self):
        return (
            "🎛️ VuTune Controls:\n"
            f"  Dashboard: {self.dashboard_url}\n"
            "  Use the dashboard to manage rooms, queue, and settings."
        )

    def _cmd_mhere(self, room_id):
        return f"🤖 VuTune is already here in room {room_id}! 🎵"

    def _cmd_silent(self):
        self._silent = not self._silent
        state = "ON (auto-messages muted)" if self._silent else "OFF"
        return f"🔇 Silent mode: {state}"

    def _cmd_mban(self, args):
        if not args:
            return "❌ Usage: !mban [username]"
        user = args.strip().lower()
        self._banned.add(user)
        return f"🚫 Banned {args} from using VuTune."

    def _cmd_munban(self, args):
        if not args:
            return "❌ Usage: !munban [username]"
        user = args.strip().lower()
        self._banned.discard(user)
        return f"✅ Unbanned {args}."

    def _cmd_help(self):
        return (
            "🎵 Commands: !play [song], !stop, !next, !prev, !add [song], "
            "!remove [song], !clear, !playlist, !playing, !listeners, !radio, !help"
        )

    # ------------------------------------------------------------------
    # Auto-message (called by RoomBot on song change if not silent)
    # ------------------------------------------------------------------

    def now_playing_msg(self, song):
        if self._silent:
            return None
        return f"🎵 Now Playing: {song['title']} | !next to skip | !help for commands"
