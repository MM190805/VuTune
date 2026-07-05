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
        Search via Invidious (free YouTube frontend API) to bypass cloud IP blocks.
        Falls back to direct yt-dlp search if Invidious is down.
        """
        import requests as req_lib

        # Public Invidious instances — tried in order until one works
        INVIDIOUS_INSTANCES = [
            "https://inv.nadeko.net",
            "https://invidious.privacydev.net",
            "https://yt.artemislena.eu",
            "https://invidious.nerdvpn.de",
            "https://invidious.io.lol",
        ]

        video_id = None
        title = query
        uploader = ""
        duration = 0

        for instance in INVIDIOUS_INSTANCES:
            try:
                resp = req_lib.get(
                    f"{instance}/api/v1/search",
                    params={"q": query, "type": "video", "sort_by": "relevance"},
                    timeout=8,
                    headers={"User-Agent": "VuTune/1.0"}
                )
                if resp.status_code == 200:
                    results = resp.json()
                    if results and len(results) > 0:
                        v = results[0]
                        video_id = v.get("videoId")
                        title = v.get("title", query)
                        uploader = v.get("author", "")
                        duration = v.get("lengthSeconds", 0)
                        logger.info(f"Found via Invidious ({instance}): {title}")
                        break
            except Exception as e:
                logger.warning(f"Invidious {instance} failed: {e}")
                continue

        if not video_id:
            logger.error("All Invidious instances failed, trying direct yt-dlp...")
            # Direct yt-dlp fallback
            try:
                ydl_opts = {
                    'format': 'bestaudio/best',
                    'noplaylist': True,
                    'quiet': True,
                    'no_warnings': True,
                    'socket_timeout': 15,
                    'http_headers': {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    },
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(f"ytsearch1:{query}", download=False)
                    if info and 'entries' in info and info['entries']:
                        entry = info['entries'][0]
                        return {
                            'title': entry.get('title', query),
                            'webpage_url': entry.get('webpage_url', ''),
                            'thumbnail': entry.get('thumbnail', ''),
                            'duration': entry.get('duration', 0),
                            'uploader': entry.get('uploader', ''),
                            'query': query,
                            'url': entry.get('url', ''),
                        }
            except Exception as e:
                logger.error(f"yt-dlp fallback also failed: {e}")
            return None

        # Use yt-dlp to get the actual stream URL from the video ID
        ydl_opts = {
            'format': 'bestaudio/best',
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'socket_timeout': 15,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
            },
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
                if not info:
                    return None
                return {
                    'title': title,
                    'webpage_url': f"https://www.youtube.com/watch?v={video_id}",
                    'thumbnail': info.get('thumbnail', ''),
                    'duration': duration,
                    'uploader': uploader,
                    'query': query,
                    'url': info.get('url', ''),
                }
        except Exception as e:
            logger.error(f"yt-dlp stream URL fetch failed: {e}")
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
