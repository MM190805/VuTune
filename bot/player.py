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
        Search using YouTube's own internal API (no API key needed).
        Get stream URL from Piped API (bypasses yt-dlp cloud IP block).
        """
        import requests as req_lib
        import json

        # ── Step 1: Search using YouTube's internal web API ──────────────────
        YT_SEARCH_URL = "https://www.youtube.com/youtubei/v1/search"
        YT_API_KEY    = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"

        payload = {
            "context": {
                "client": {
                    "clientName": "WEB",
                    "clientVersion": "2.20230810.00.00",
                    "hl": "en",
                    "gl": "US",
                }
            },
            "query": query,
        }
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "X-YouTube-Client-Name": "1",
            "X-YouTube-Client-Version": "2.20230810.00.00",
        }

        video_id = None
        title = query
        uploader = ""
        duration = 0

        try:
            resp = req_lib.post(
                f"{YT_SEARCH_URL}?key={YT_API_KEY}",
                json=payload,
                headers=headers,
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                # Navigate YouTube's response JSON to find the first video
                contents = (
                    data.get("contents", {})
                        .get("twoColumnSearchResultsRenderer", {})
                        .get("primaryContents", {})
                        .get("sectionListRenderer", {})
                        .get("contents", [])
                )
                for section in contents:
                    items = section.get("itemSectionRenderer", {}).get("contents", [])
                    for item in items:
                        vr = item.get("videoRenderer", {})
                        if vr.get("videoId"):
                            video_id = vr["videoId"]
                            title = vr.get("title", {}).get("runs", [{}])[0].get("text", query)
                            uploader = vr.get("ownerText", {}).get("runs", [{}])[0].get("text", "")
                            dur_text = vr.get("lengthText", {}).get("simpleText", "0:00")
                            try:
                                parts = dur_text.split(":")
                                duration = sum(int(p) * 60**i for i, p in enumerate(reversed(parts)))
                            except Exception:
                                duration = 0
                            logger.info(f"YouTube internal search found: {title} [{video_id}]")
                            break
                    if video_id:
                        break
        except Exception as e:
            logger.warning(f"YouTube internal API failed: {e}")

        # ── Step 2: Get stream URL ────────────────────────────────────────────
        if video_id:
            yt_url = f"https://www.youtube.com/watch?v={video_id}"

            # 2a: Try yt-dlp with direct video URL (YouTube IS reachable from Render)
            try:
                ydl_opts = {
                    'format': 'bestaudio/best',
                    'noplaylist': True,
                    'quiet': True,
                    'no_warnings': True,
                    'socket_timeout': 20,
                    'http_headers': {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
                        'Accept-Language': 'en-US,en;q=0.9',
                    },
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(yt_url, download=False)
                    if info and info.get('url'):
                        logger.info(f"Got stream via yt-dlp direct URL: {title}")
                        return {
                            "title":       title,
                            "webpage_url": yt_url,
                            "thumbnail":   f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                            "duration":    duration,
                            "uploader":    uploader,
                            "query":       query,
                            "url":         info['url'],
                        }
            except Exception as e:
                logger.warning(f"yt-dlp direct URL failed: {e}")

            # 2b: Try Piped API instances as fallback
            PIPED_INSTANCES = [
                "https://pipedapi.kavin.rocks",
                "https://pipedapi.in.projectsegfau.lt",
                "https://piped-api.codespace.cz",
                "https://watchapi.whatever.social",
                "https://pipedapi.moomoo.me",
            ]
            for piped in PIPED_INSTANCES:
                try:
                    pr = req_lib.get(
                        f"{piped}/streams/{video_id}",
                        timeout=10,
                        headers={"User-Agent": "VuTune/1.0"}
                    )
                    if pr.status_code != 200:
                        logger.warning(f"Piped {piped} returned {pr.status_code}")
                        continue
                    pdata = pr.json()
                    stream_url = None
                    best_br = 0
                    for s in pdata.get("audioStreams", []):
                        if s.get("url") and s.get("bitrate", 0) > best_br:
                            best_br = s["bitrate"]
                            stream_url = s["url"]
                    if not stream_url:
                        for s in pdata.get("videoStreams", []):
                            if s.get("url"):
                                stream_url = s["url"]
                                break
                    if stream_url:
                        logger.info(f"Got stream from Piped ({piped}): {title}")
                        return {
                            "title":       title,
                            "webpage_url": yt_url,
                            "thumbnail":   f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                            "duration":    duration,
                            "uploader":    uploader,
                            "query":       query,
                            "url":         stream_url,
                        }
                except Exception as e:
                    logger.warning(f"Piped {piped} failed: {e}")
                    continue

        logger.error(f"All search/stream methods failed for: {query}")
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
