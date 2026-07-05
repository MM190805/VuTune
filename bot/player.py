"""
VuTune - IMVU Music Bot
Music Player

Searches YouTube via yt-dlp and streams audio through
ffmpeg directly to an Icecast server (no files saved to disk).
"""

import subprocess
import threading
import logging
import yt_dlp
import sys
from dashboard import app

logger = logging.getLogger(__name__)


class MusicPlayer:
    def __init__(self, icecast_config: dict):
        self.ic = icecast_config
        self._ytdl_proc = None
        self._ffmpeg_proc = None
        self.current_song = None
        self.is_playing = False
        self.on_song_end = None   # callback() when a song finishes naturally

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _monitor(self):
        """Read ffmpeg stdout and push audio chunks to the radio server."""
        radio_url = os.environ.get("RADIO_SERVER_URL", "").rstrip("/")
        radio_secret = os.environ.get("RADIO_PUSH_SECRET", "vutune-radio-secret")
        push_url = f"{radio_url}/push" if radio_url else None

        if self._ffmpeg_proc:
            while True:
                chunk = self._ffmpeg_proc.stdout.read(4096)
                if not chunk:
                    break
                if push_url:
                    try:
                        import urllib.request
                        req = urllib.request.Request(
                            push_url,
                            data=chunk,
                            method='POST',
                            headers={
                                'Content-Type': 'audio/mpeg',
                                'X-Radio-Secret': radio_secret,
                            }
                        )
                        urllib.request.urlopen(req, timeout=2)
                    except Exception:
                        pass  # Don't crash the player if radio server is down
                else:
                    # Fallback: broadcast locally if no radio server configured
                    try:
                        from dashboard.app import broadcast_audio
                        broadcast_audio(chunk)
                    except Exception:
                        pass
            self._ffmpeg_proc.wait()
        self.is_playing = False
        logger.info("Song finished.")
        if self.on_song_end:
            self.on_song_end()


    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def search_youtube(self, query: str) -> dict | None:
        """
        Search YouTube for a song by name (all languages supported).
        Returns song info dict or None on failure.
        """
        ydl_opts = {
            'format': 'bestaudio/best',
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'default_search': 'ytsearch',
            # Bypass YouTube's bot detection on cloud IPs
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            },
            # Skip age-gate and other bot checks
            'extractor_args': {
                'youtube': {
                    'skip': ['hls', 'dash'],
                    'player_skip': ['js', 'configs', 'webpage'],
                }
            },
            'socket_timeout': 15,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"ytsearch1:{query}", download=False)
                if not info or 'entries' not in info or not info['entries']:
                    return None
                entry = info['entries'][0]
                if not entry:
                    return None
                return {
                    'title':       entry.get('title', query),
                    'webpage_url': entry.get('webpage_url', ''),
                    'thumbnail':   entry.get('thumbnail', ''),
                    'duration':    entry.get('duration', 0),
                    'uploader':    entry.get('uploader', ''),
                    'query':       query,
                    'url':         entry.get('url', ''),
                }
        except Exception as e:
            logger.error(f"YouTube search error: {e}")
            return None


    def play(self, song: dict):
        """Stream a song to internal broadcast. Stops any current song first."""
        self.stop()

        logger.info(f"▶ Playing: {song['title']}")
        self.current_song = song
        self.is_playing = True

        # ffmpeg reads from the direct URL and pipes to stdout
        ffmpeg_cmd = [
            'ffmpeg',
            '-re',                      # read at native speed (real-time)
            '-i', song['url'],          # direct media URL
            '-acodec', 'libmp3lame',
            '-ab', '128k',
            '-ar', '44100',
            '-f', 'mp3',
            'pipe:1',
        ]

        try:
            self._ffmpeg_proc = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            # Monitor in background thread so we know when the song ends
            threading.Thread(target=self._monitor, daemon=True).start()

        except FileNotFoundError as e:
            logger.error(
                f"Process not found — make sure ffmpeg is installed: {e}"
            )
            self.is_playing = False
            self.current_song = None

    def stop(self):
        """Stop current playback immediately."""
        if self._ffmpeg_proc:
            self._ffmpeg_proc.terminate()
            self._ffmpeg_proc = None
        self.is_playing = False
        self.current_song = None

    def get_status(self) -> dict:
        return {
            'is_playing': self.is_playing,
            'current_song': self.current_song,
        }
