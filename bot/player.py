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
        """Wait for ffmpeg to finish streaming to Icecast."""
        if self._ffmpeg_proc:
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
        Use yt-dlp to search and extract the best audio stream.
        This handles all recent YouTube API changes automatically.
        Includes a fallback to SoundCloud if YouTube blocks the datacenter IP.
        """
        def _do_search(search_prefix: str):
            ydl_opts = {
                'format': 'bestaudio/best',
                'noplaylist': True,
                'quiet': True,
                'default_search': search_prefix,
                'extract_flat': False,
                'match_filter': lambda info, *args, **kwargs: "Too short" if info.get('duration') and info.get('duration') < 60 else None
            }
            import os
            cookie_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'cookies.txt')
            if os.path.exists(cookie_path):
                ydl_opts['cookiefile'] = cookie_path
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # If we're searching soundcloud, grab 5 results so we can filter out 30s premium previews
                search_query = f"{search_prefix}:{query}"
                if search_prefix == 'scsearch':
                    search_query = f"scsearch5:{query}"
                    
                info = ydl.extract_info(search_query, download=False)
                if 'entries' in info and len(info['entries']) > 0:
                    # Find the first valid entry (match_filter turns rejected ones to None)
                    for entry in info['entries']:
                        if entry is not None:
                            logger.info(f"Search found: {entry.get('title')} [{entry.get('id')}]")
                            return {
                                "title":       entry.get('title', query),
                                "webpage_url": entry.get('webpage_url', ''),
                                "thumbnail":   entry.get('thumbnail', ''),
                                "duration":    entry.get('duration', 0),
                                "uploader":    entry.get('uploader', ''),
                                "query":       query,
                                "url":         entry.get('url', ''),
                            }
            return None

        try:
            # Try YouTube first
            result = _do_search('ytsearch')
            if result: return result
        except Exception as e:
            logger.warning(f"YouTube search failed (likely bot block), falling back to SoundCloud: {e}")
            try:
                # Fallback to SoundCloud
                result = _do_search('scsearch')
                if result: return result
            except Exception as e2:
                logger.error(f"SoundCloud fallback also failed: {e2}")
        
        logger.error(f"All search/stream methods failed for: {query}")
        return None


    def play(self, song: dict):
        """Stream a song to internal broadcast. Stops any current song first."""
        self.stop()

        logger.info(f"▶ Playing: {song['title']}")
        self.current_song = song
        self.is_playing = True

        # ffmpeg streams directly to Icecast
        ffmpeg_cmd = [
            'ffmpeg',
            '-re',                      # read at native speed (real-time)
            '-i', song['url'],          # direct media URL
            '-acodec', 'libmp3lame',
            '-ab', '128k',
            '-ar', '44100',
            '-content_type', 'audio/mpeg',
            '-f', 'mp3',
            'icecast://source:vutune_radio_secret@localhost:8000/stream',
        ]

        try:
            self._ffmpeg_proc = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.DEVNULL,
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
