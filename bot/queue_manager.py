"""
VuTune - IMVU Music Bot
Queue Manager

Thread-safe song queue with add, skip, remove, and clear operations.
"""

import threading
from collections import deque


class QueueManager:
    def __init__(self):
        self._queue: deque = deque()
        self._lock = threading.Lock()

    def add(self, song: dict):
        with self._lock:
            self._queue.append(song)

    def next(self) -> dict | None:
        with self._lock:
            return self._queue.popleft() if self._queue else None

    def peek(self) -> dict | None:
        """Return next song without removing it."""
        with self._lock:
            return self._queue[0] if self._queue else None

    def remove(self, index: int) -> dict | None:
        """Remove song at 1-based index. Returns removed song or None."""
        with self._lock:
            lst = list(self._queue)
            if 1 <= index <= len(lst):
                removed = lst.pop(index - 1)
                self._queue = deque(lst)
                return removed
            return None

    def clear(self):
        with self._lock:
            self._queue.clear()

    def list(self) -> list:
        with self._lock:
            return list(self._queue)

    def size(self) -> int:
        with self._lock:
            return len(self._queue)

    def is_empty(self) -> bool:
        with self._lock:
            return len(self._queue) == 0
