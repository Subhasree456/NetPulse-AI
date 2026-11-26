"""Small in-process sliding-window rate limiter.

NetPulse runs as a single long-lived FastAPI process for live monitoring, so an
in-process limiter is sufficient for protecting the agent ingest endpoints from
accidental floods. In a multi-instance deployment each instance enforces its own
share of the quota, which is still bounded by the per-instance configuration.
"""

from __future__ import annotations

import threading
import time
from collections import deque


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: int = 60) -> None:
        self.limit = max(1, int(limit))
        self.window = max(1, int(window_seconds))
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, int]:
        """Return (allowed, remaining)."""
        now = time.monotonic()
        cutoff = now - self.window
        with self._lock:
            bucket = self._events.setdefault(key, deque())
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self.limit:
                return False, 0
            bucket.append(now)
            return True, max(0, self.limit - len(bucket))

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._events.clear()
            else:
                self._events.pop(key, None)
