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
        Search via Invidious API and get stream URL from Invidious too.
        Completely bypasses YouTube's cloud IP blocks — no yt-dlp needed for search.
        """
        import requests as req_lib

        # Public Invidious instances — tried in order until one works
        INVIDIOUS_INSTANCES = [
            "https://inv.nadeko.net",
            "https://invidious.privacydev.net",
            "https://yt.artemislena.eu",
            "https://invidious.nerdvpn.de",
            "https://invidious.io.lol",
            "https://invidious.fdn.fr",
        ]

        for instance in INVIDIOUS_INSTANCES:
            try:
                # Step 1: Search for the video
                resp = req_lib.get(
                    f"{instance}/api/v1/search",
                    params={"q": query, "type": "video", "sort_by": "relevance"},
                    timeout=8,
                    headers={"User-Agent": "VuTune/1.0"}
                )
                if resp.status_code != 200:
                    continue
                results = resp.json()
                if not results:
                    continue

                video = results[0]
                video_id = video.get("videoId")
                if not video_id:
                    continue

                title = video.get("title", query)
                uploader = video.get("author", "")
                duration = video.get("lengthSeconds", 0)
                logger.info(f"Found via Invidious ({instance}): {title} [{video_id}]")

                # Step 2: Get stream URL from the same Invidious instance
                vid_resp = req_lib.get(
                    f"{instance}/api/v1/videos/{video_id}",
                    params={"fields": "adaptiveFormats,formatStreams"},
                    timeout=10,
                    headers={"User-Agent": "VuTune/1.0"}
                )
                if vid_resp.status_code != 200:
                    continue

                vid_info = vid_resp.json()

                # Try adaptive formats first (best audio quality)
                stream_url = None
                best_bitrate = 0
                for fmt in vid_info.get("adaptiveFormats", []):
                    if "audio" in fmt.get("type", "") and fmt.get("url"):
                        bitrate = fmt.get("bitrate", 0)
                        if bitrate > best_bitrate:
                            best_bitrate = bitrate
                            stream_url = fmt["url"]

                # Fallback to regular format streams
                if not stream_url:
                    for fmt in vid_info.get("formatStreams", []):
                        if fmt.get("url"):
                            stream_url = fmt["url"]
                            break

                if not stream_url:
                    logger.warning(f"No stream URL from {instance}, trying next...")
                    continue

                logger.info(f"Got stream URL from Invidious. Title: {title}")
                return {
                    'title': title,
                    'webpage_url': f"https://www.youtube.com/watch?v={video_id}",
                    'thumbnail': f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                    'duration': duration,
                    'uploader': uploader,
                    'query': query,
                    'url': stream_url,
                }

            except Exception as e:
                logger.warning(f"Invidious {instance} failed: {e}")
                continue

        logger.error("All Invidious instances failed.")
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
