"""Redis-backed rate limit decisions for a standalone Redis server."""

from math import ceil
from typing import cast
from uuid import uuid4

from aiogram_extensions._validation import ConfigurationError
from aiogram_extensions.redis import RedisConnectionManager, RedisLifecycleError

from ._backend import BackendDecision, Buckets, Key
from .errors import RateLimitBackendError

# Values and absolute timestamps must fit exactly in a Lua double.
_MAX_WINDOW_MS = 2**52 - 1

_CHECK = """
local time = redis.call('TIME')
local now = tonumber(time[1]) * 1000 + math.floor(tonumber(time[2]) / 1000)
local has_cooldown = tonumber(ARGV[1])
local first_rule = 1 + has_cooldown
local delays = {0}
if has_cooldown == 1 then
    local ttl = redis.call('PTTL', KEYS[1])
    if ttl >= 0 then
        delays[1] = math.max(1, ttl)
    elseif ttl == -1 then
        return redis.error_reply('Invalid cooldown state')
    end
end
local blocked = delays[1] > 0
local counts = {}

-- Read every rule before charging any. Expired sliding entries do not count.
for i = first_rule, #KEYS do
    local arg = 2 + (i - first_rule) * 3
    local sliding = ARGV[arg + 1] == 'sliding_window'
    local window = tonumber(ARGV[arg + 2])
    local limit = tonumber(ARGV[arg + 3])
    local delay = 0
    if sliding then
        local cutoff = '(' .. string.format('%.0f', now - window)
        if redis.call('ZCOUNT', KEYS[i], cutoff, '+inf') >= limit then
            local oldest = redis.call('ZRANGEBYSCORE', KEYS[i], cutoff, '+inf',
                                      'WITHSCORES', 'LIMIT', 0, 1)
            delay = tonumber(oldest[2]) + window - now
        end
    else
        local count = redis.call('GET', KEYS[i])
        if count then
            local ttl = redis.call('PTTL', KEYS[i])
            if not string.match(count, '^[1-9]%d*$') or tonumber(count) >= 2^53 or ttl < 0 then
                return redis.error_reply('Invalid fixed-window state')
            end
            counts[i] = tonumber(count)
            if counts[i] >= limit then
                delay = math.max(1, ttl)
            end
        end
    end
    delays[#delays + 1] = delay
    blocked = blocked or delay > 0
end

if not blocked then
    local writes = {}
    local function plan_write(...)
        -- A Lua error does not roll back earlier writes.
        if not redis.acl_check_cmd(...) then
            error('Rate limit write not permitted')
        end
        writes[#writes + 1] = {...}
    end

    for i = first_rule, #KEYS do
        local arg = 2 + (i - first_rule) * 3
        local duration = ARGV[arg + 2]
        if ARGV[arg + 1] == 'sliding_window' then
            plan_write('ZREMRANGEBYSCORE', KEYS[i], '-inf',
                       string.format('%.0f', now - tonumber(duration)))
            plan_write('ZADD', KEYS[i], string.format('%.0f', now), ARGV[2])
            plan_write('PEXPIRE', KEYS[i], duration)
        elseif counts[i] then
            plan_write('INCR', KEYS[i])
        else
            plan_write('SET', KEYS[i], 1, 'PX', duration)
        end
    end
    for _, command in ipairs(writes) do
        redis.call(unpack(command))
    end
end
return delays
"""

_COOLDOWN = """
if redis.call('PTTL', KEYS[1]) < tonumber(ARGV[1]) then
    redis.call('SET', KEYS[1], 1, 'PX', ARGV[1])
end
return 1
"""


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
            raise RateLimitBackendError("Redis rate limit manager is unavailable") from None  # noqa: TRY003

        from redis.exceptions import RedisError  # noqa: PLC0415 -- Optional dependency.

        try:
            # redis-py overloads EVAL for synchronous and asynchronous clients.
            return await client.eval(script, len(keys), *keys, *args)
        except RedisError:
            raise RateLimitBackendError("Redis rate limit operation failed") from None  # noqa: TRY003

    async def check(self, buckets: Buckets, cooldown_key: Key | None) -> BackendDecision:
        keys = [self._manager.key("rate_limit", *cooldown_key)] if cooldown_key else []
        args: list[str | int] = [
            int(cooldown_key is not None),
            uuid4().hex if any(rule.algorithm == "sliding_window" for _, rule in buckets) else "",
        ]
        for key, rule in buckets:
            keys.append(self._manager.key("rate_limit", *key))
            args.extend((rule.algorithm, _milliseconds(rule.window), str(rule.limit)))

        delays = cast("list[int]", await self._eval(_CHECK, keys, *args))
        blocked = tuple(
            (rule.name, delay / 1000)
            for (_, rule), delay in zip(buckets, delays[1:], strict=True)
            if delay > 0
        )
        return BackendDecision(blocked_rules=blocked, cooldown=delays[0] / 1000)

    async def cooldown(self, key: Key, duration: float) -> None:
        await self._eval(
            _COOLDOWN, [self._manager.key("rate_limit", *key)], _milliseconds(duration)
        )
