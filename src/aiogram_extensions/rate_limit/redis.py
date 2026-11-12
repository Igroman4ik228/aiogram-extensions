"""Redis-backed rate limit decisions for a standalone Redis server."""

from functools import cache
from importlib.resources import files
from math import ceil
from typing import TypeGuard, cast
from uuid import uuid4

from aiogram_extensions._validation import ConfigurationError
from aiogram_extensions.redis import RedisConnectionManager, RedisLifecycleError

from ._backend import BackendDecision, Buckets, Key
from .errors import RateLimitBackendError

# Values and absolute timestamps must fit exactly in a Lua double.
_MAX_WINDOW_MS = 2**52 - 1


@cache
def _script(name: str) -> str:
    return files("aiogram_extensions.rate_limit").joinpath(name).read_text(encoding="utf-8")


def _valid_delays(reply: object, count: int) -> TypeGuard[list[int]]:
    if not isinstance(reply, list):
        return False

    delays = cast("list[object]", reply)
    return len(delays) == count and all(type(delay) is int and delay >= 0 for delay in delays)


def _milliseconds(value: float) -> int:
    if value > _MAX_WINDOW_MS / 1000:
        raise ConfigurationError("duration", "exceeds the supported Redis range")

    return max(1, ceil(value * 1000))


class RedisRateLimitBackend:
    """Use a started connection manager without taking ownership of its client."""

    def __init__(self, manager: RedisConnectionManager) -> None:
        self._manager = manager

    async def _eval(self, script: str, keys: list[str], *args: str | int) -> object:
        try:
            client = self._manager.client
        except RedisLifecycleError:
            raise RateLimitBackendError("Redis rate limit manager is unavailable") from None

        from redis.exceptions import RedisError  # noqa: PLC0415 -- Optional dependency.

        try:
            # redis-py overloads EVAL for synchronous and asynchronous clients.
            return await client.eval(script, len(keys), *keys, *args)
        except RedisError:
            raise RateLimitBackendError("Redis rate limit operation failed") from None

    async def check(self, buckets: Buckets, cooldown_key: Key | None) -> BackendDecision:
        keys = [self._manager.key("rate_limit", *cooldown_key)] if cooldown_key else []
        args: list[str | int] = [
            int(cooldown_key is not None),
            uuid4().hex if any(rule.algorithm == "sliding_window" for _, rule in buckets) else "",
        ]

        for key, rule in buckets:
            keys.append(self._manager.key("rate_limit", *key))
            args.extend((rule.algorithm, _milliseconds(rule.window), str(rule.limit)))

        delays = await self._eval(_script("_check.lua"), keys, *args)
        if not _valid_delays(delays, len(buckets) + 1):
            raise RateLimitBackendError("Redis returned an invalid rate limit reply")

        blocked = tuple(
            (rule.name, delay / 1000)
            for (_, rule), delay in zip(buckets, delays[1:], strict=True)
            if delay > 0
        )

        return BackendDecision(blocked_rules=blocked, cooldown=delays[0] / 1000)

    async def cooldown(self, key: Key, duration: float) -> None:
        await self._eval(
            _script("_cooldown.lua"),
            [self._manager.key("rate_limit", *key)],
            _milliseconds(duration),
        )
