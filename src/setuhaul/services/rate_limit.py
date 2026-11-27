"""Per-key rate limiting (sliding window), in memory.

One process only: each uvicorn worker keeps its own counts. At scale this moves
to Redis (or API Gateway / WAF rate rules) so all workers share one count.
"""

import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable


class RateLimiter:
    """Allow at most `limit` hits per `window_s` seconds for each key."""

    def __init__(self, limit: int, window_s: float, clock: Callable[[], float] = time.monotonic):
        self._limit = limit
        self._window_s = window_s
        self._clock = clock                       # injectable, so tests don't sleep
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()             # FastAPI runs sync routes in a thread pool

    def hit(self, key: str) -> float:
        """Record one hit for `key`. Returns 0 if allowed, else seconds to wait."""
        now = self._clock()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] >= self._window_s:   # forget hits older than the window
                hits.popleft()
            if len(hits) >= self._limit:
                return self._window_s - (now - hits[0])       # when the oldest hit expires
            hits.append(now)
            return 0.0