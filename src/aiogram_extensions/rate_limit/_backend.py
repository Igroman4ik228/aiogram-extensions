"""Storage contract shared by the rate limiter and its backends."""

from dataclasses import dataclass
from typing import Protocol

from .config import RateRule

Key = tuple[str, ...]
Buckets = tuple[tuple[Key, RateRule], ...]


@dataclass(frozen=True, slots=True)
class BackendDecision:
    blocked_rules: tuple[tuple[str, float], ...] = ()
    cooldown: float = 0.0


class Backend(Protocol):
    async def check(self, buckets: Buckets, cooldown_key: Key | None) -> BackendDecision: ...

    async def cooldown(self, key: Key, duration: float) -> None: ...
