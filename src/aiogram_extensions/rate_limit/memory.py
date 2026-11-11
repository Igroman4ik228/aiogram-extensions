"""Process-local counters using a monotonic clock."""

import asyncio
import heapq
import math
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from ._backend import BackendDecision, Buckets, Key

_CLEANUP_BATCH = 64


def _deadline(start: float, duration: float) -> float:
    deadline = start + duration
    return deadline if deadline > start else math.nextafter(start, math.inf)


@dataclass(slots=True)
class _Window:
    expires_at: float
    count: int = 0
    expirations: deque[float] | None = None

    def delay(self, limit: int, now: float) -> float:
        expirations = self.expirations
        if expirations is None:
            return self.expires_at - now if self.count >= limit else 0.0

        while expirations and expirations[0] <= now:
            expirations.popleft()

        if len(expirations) >= limit:
            return expirations[0] - now

        return 0.0

    def charge(self, now: float, duration: float) -> None:
        if self.expirations is None:
            self.count += 1
        else:
            self.expires_at = _deadline(now, duration)
            self.expirations.append(self.expires_at)


class MemoryRateLimitBackend:
    """Share one instance across limiters on the same event loop to share budgets."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = asyncio.Lock()
        self._windows: dict[Key, _Window] = {}
        self._expiry: list[tuple[float, Key]] = []

    def _cleanup(self, now: float) -> None:
        for _ in range(_CLEANUP_BATCH):
            if not self._expiry or self._expiry[0][0] > now:
                break

            _, key = heapq.heappop(self._expiry)
            expires_at = self._windows[key].expires_at
            if expires_at <= now:
                del self._windows[key]
            else:
                heapq.heappush(self._expiry, (expires_at, key))

    def _store(self, key: Key, window: _Window) -> None:
        if key not in self._windows:
            heapq.heappush(self._expiry, (window.expires_at, key))
        self._windows[key] = window

    async def check(self, buckets: Buckets, cooldown_key: Key | None) -> BackendDecision:
        async with self._lock:
            now = self._clock()
            self._cleanup(now)

            cooldown = self._windows.get(cooldown_key) if cooldown_key is not None else None
            cooldown_delay = max(0.0, cooldown.expires_at - now) if cooldown else 0.0

            blocked: list[tuple[str, float]] = []
            for key, rule in buckets:
                window = self._windows.get(key)
                if window is not None and window.expires_at > now:
                    retry_after = window.delay(rule.limit, now)
                    if retry_after > 0:
                        blocked.append((rule.name, retry_after))

            if not blocked and not cooldown_delay:
                for key, rule in buckets:
                    window = self._windows.get(key)
                    if window is None or window.expires_at <= now:
                        window = _Window(
                            _deadline(now, rule.window),
                            expirations=deque() if rule.algorithm == "sliding_window" else None,
                        )
                        self._store(key, window)

                    window.charge(now, rule.window)

            return BackendDecision(blocked_rules=tuple(blocked), cooldown=cooldown_delay)

    async def cooldown(self, key: Key, duration: float) -> None:
        async with self._lock:
            now = self._clock()
            self._cleanup(now)

            deadline = _deadline(now, duration)
            current = self._windows.get(key)
            if current is None or current.expires_at < deadline:
                self._store(key, _Window(deadline))
