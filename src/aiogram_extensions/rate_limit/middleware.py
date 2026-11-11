"""aiogram adapters for incoming updates and outgoing Bot API requests."""

from collections.abc import Awaitable, Callable, Collection
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.client.session.middlewares.base import BaseRequestMiddleware, NextRequestMiddlewareType
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import Response, TelegramMethod
from aiogram.methods.base import TelegramType
from aiogram.types import Chat, TelegramObject, User

from aiogram_extensions._validation import ConfigurationError

from .errors import RateLimitExceededError
from .limiter import RateLimitContext, RateLimiter


class IncomingRateLimitMiddleware(BaseMiddleware):
    """Limit updates with both a user and a chat, using dispatcher context."""

    def __init__(
        self,
        limiter: RateLimiter,
        *,
        allowed_users: Collection[int] = (),
        allowed_chats: Collection[int] = (),
        exempt: Callable[[TelegramObject], bool] | None = None,
    ) -> None:
        if limiter.direction != "incoming":
            raise ConfigurationError("limiter", "must use incoming direction")
        if any(
            rule.scope not in ("user_chat", "bot", "chat", "group") for rule in limiter.config.rules
        ):
            raise ConfigurationError("scope", "is unsupported by incoming middleware")
        self._limiter = limiter
        self._allowed_users = frozenset(allowed_users)
        self._allowed_chats = frozenset(allowed_chats)
        self._exempt = exempt

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        chat = data.get("event_chat")
        if (
            not isinstance(user, User)
            or not isinstance(chat, Chat)
            or user.id in self._allowed_users
            or chat.id in self._allowed_chats
            or (self._exempt is not None and self._exempt(event))
        ):
            return await handler(event, data)

        bot: Bot = data["bot"]
        result = await self._limiter.acquire(
            RateLimitContext(bot_id=bot.id, user_id=user.id, chat_id=chat.id, chat_type=chat.type)
        )
        if result.allowed:
            return await handler(event, data)
        return None


class OutgoingRateLimitMiddleware(BaseRequestMiddleware):
    """Apply outgoing rules before the request and record Telegram cooldowns."""

    def __init__(
        self,
        limiter: RateLimiter,
        *,
        chat_type: Callable[[Bot, int | str], str | None] | None = None,
        custom_key: Callable[[Bot, TelegramMethod[Any]], str | None] | None = None,
    ) -> None:
        if limiter.direction != "outgoing":
            raise ConfigurationError("limiter", "must use outgoing direction")
        scopes = {rule.scope for rule in limiter.config.rules}
        if "user_chat" in scopes:
            raise ConfigurationError("scope", "user_chat is unsupported by outgoing middleware")
        if "group" in scopes and chat_type is None:
            raise ConfigurationError("chat_type", "resolver is required for group rules")
        if "custom" in scopes and custom_key is None:
            raise ConfigurationError("custom_key", "resolver is required for custom rules")
        self._limiter = limiter
        self._chat_type = chat_type if "group" in scopes else None
        self._custom_key = custom_key if "custom" in scopes else None

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> Response[TelegramType]:
        chat_id = getattr(method, "chat_id", None)
        context = RateLimitContext(
            bot_id=bot.id,
            chat_id=chat_id,
            chat_type=(
                self._chat_type(bot, chat_id)
                if self._chat_type is not None and chat_id is not None
                else None
            ),
            method=method.__api_method__,
            custom_key=self._custom_key(bot, method) if self._custom_key is not None else None,
        )
        result = await self._limiter._acquire(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
            context, allow_missing_scope=True
        )
        if not result.allowed:
            raise RateLimitExceededError(result)

        try:
            return await make_request(bot, method)
        except TelegramRetryAfter as exc:
            await self._limiter._record_cooldown(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
                bot.id, exc.retry_after
            )
            raise
