"""Resolve rule scopes and apply the configured rejection or wait policy."""

import asyncio
from dataclasses import dataclass
from typing import Literal

from aiogram_extensions._validation import (
    ConfigurationError,
    nonempty_string,
    positive_integer,
    seconds,
)
from aiogram_extensions.integrations import BackendErrorEvent, EventDispatcher, LimitExceededEvent

from ._backend import Backend, Buckets
from .config import RateLimitConfig, RateRule
from .errors import RateLimitBackendError
from .memory import MemoryRateLimitBackend
from .result import RateLimitResult

Direction = Literal["incoming", "outgoing"]
_COOLDOWN_RULE = "telegram_retry_after"


@dataclass(frozen=True, kw_only=True)
class RateLimitContext:
    bot_id: int
    user_id: int | None = None
    chat_id: int | str | None = None
    chat_type: str | None = None
    method: str | None = None
    custom_key: str | None = None

    def __post_init__(self) -> None:
        positive_integer("bot_id", self.bot_id)

        if self.user_id is not None and type(self.user_id) is not int:
            raise ConfigurationError("user_id", "must be an integer")

        if self.chat_id is not None and type(self.chat_id) is not int:
            nonempty_string("chat_id", self.chat_id)

        for name, value in (
            ("chat_type", self.chat_type),
            ("method", self.method),
            ("custom_key", self.custom_key),
        ):
            if value is not None:
                nonempty_string(name, value)


def _scope_parts(
    rule: RateRule, context: RateLimitContext, *, allow_missing_scope: bool
) -> tuple[str, ...] | None:
    if rule.scope == "group" and context.chat_type not in ("group", "supergroup"):
        return None

    match rule.scope:
        case "bot":
            values = ()
        case "chat" | "group":
            values = (context.chat_id,)
        case "user_chat":
            values = (context.user_id, context.chat_id)
        case "method":
            values = (context.method,)
        case "custom":
            values = (context.custom_key,)

    if None in values:
        if allow_missing_scope:
            return None
        raise ConfigurationError(rule.scope, "requires additional rate limit context")

    return tuple(str(value) for value in values)


class RateLimiter:
    """Check and charge all applicable rules in one backend operation."""

    def __init__(
        self,
        config: RateLimitConfig,
        *,
        direction: Direction,
        backend: Backend | None = None,
        events: EventDispatcher | None = None,
    ) -> None:
        if direction not in ("incoming", "outgoing"):
            raise ConfigurationError("direction", "must be incoming or outgoing")

        if direction == "outgoing" and any(rule.name == _COOLDOWN_RULE for rule in config.rules):
            raise ConfigurationError("name", f"{_COOLDOWN_RULE} is reserved")

        self._config = config
        self._direction: Direction = direction
        self._backend = MemoryRateLimitBackend() if backend is None else backend
        self._events = EventDispatcher() if events is None else events

    @property
    def config(self) -> RateLimitConfig:
        return self._config

    @property
    def direction(self) -> Direction:
        return self._direction

    async def check(self, context: RateLimitContext) -> RateLimitResult:
        """Try once, regardless of the configured on_limit policy."""
        return await self._check(context.bot_id, self._buckets(context))

    async def acquire(self, context: RateLimitContext) -> RateLimitResult:
        """Check and, in wait mode, sleep and recheck until admitted."""
        return await self._acquire(context)

    async def _acquire(
        self, context: RateLimitContext, *, allow_missing_scope: bool = False
    ) -> RateLimitResult:
        buckets = self._buckets(context, allow_missing_scope=allow_missing_scope)

        while True:
            result = await self._check(context.bot_id, buckets)
            if result.allowed or self.config.on_limit == "reject":
                return result

            await asyncio.sleep(result.retry_after)

    def _buckets(self, context: RateLimitContext, *, allow_missing_scope: bool = False) -> Buckets:
        prefix = (self.direction, str(context.bot_id))
        return tuple(
            ((*prefix, rule.name, rule.algorithm, rule.scope, *parts), rule)
            for rule in self.config.rules
            if (parts := _scope_parts(rule, context, allow_missing_scope=allow_missing_scope))
            is not None
        )

    async def _check(self, bot_id: int, buckets: Buckets) -> RateLimitResult:
        cooldown_key = (self.direction, str(bot_id)) if self.direction == "outgoing" else None
        if not buckets and cooldown_key is None:
            return RateLimitResult(allowed=True)

        try:
            decision = await self._backend.check(buckets, cooldown_key)
        except RateLimitBackendError:
            await self._events.emit(BackendErrorEvent(component="rate_limit", operation="check"))
            raise

        blocked = decision.blocked_rules
        if decision.cooldown:
            blocked = (*blocked, (_COOLDOWN_RULE, decision.cooldown))

        if not blocked:
            return RateLimitResult(allowed=True)

        triggered = tuple(name for name, _ in blocked)
        delay = max(delay for _, delay in blocked)

        await self._events.emit(
            LimitExceededEvent(
                direction=self.direction, triggered_rules=triggered, retry_after=delay
            )
        )

        return RateLimitResult(allowed=False, retry_after=delay, triggered_rules=triggered)

    async def _record_cooldown(self, bot_id: int, duration: float) -> None:
        seconds("retry_after", duration, allow_zero=True)
        if not duration:
            return

        try:
            await self._backend.cooldown(("outgoing", str(bot_id)), duration)
        except RateLimitBackendError:
            await self._events.emit(BackendErrorEvent(component="rate_limit", operation="check"))
            raise
