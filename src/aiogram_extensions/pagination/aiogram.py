"""Inline navigation for aiogram with bounded, expiring callback references."""

import asyncio
import re
import secrets
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, Literal, TypeVar
from weakref import WeakValueDictionary

from aiogram import Bot, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    MessageEntity,
)
from aiogram.utils.formatting import Text

from aiogram_extensions._validation import (
    identifier,
    nonempty_string,
    positive_integer,
    require_instance,
    seconds,
)
from aiogram_extensions.pagination.core import (
    CursorPage,
    CursorPaginator,
    InvalidCursorError,
    Page,
    PageOutOfRangeError,
    PaginationError,
    Paginator,
)

__all__ = [
    "InvalidNavigationError",
    "Navigation",
    "NavigationConfig",
    "StaleNavigationError",
    "TextPagination",
]

T = TypeVar("T")
Button = Literal["previous", "current", "next"]
_MAX_PREFIX_LENGTH = 32
_BUTTON_COUNT = 3


class InvalidNavigationError(PaginationError):
    """Callback data does not match the navigation format."""

    def __init__(self) -> None:
        super().__init__("navigation", "has invalid callback data")


class StaleNavigationError(PaginationError):
    """The callback is stale, unknown or belongs to another scope."""

    def __init__(self) -> None:
        super().__init__("navigation", "is stale; rebuild the keyboard")


def _current_label(
    template: str, *, page: int | str, pages: int | str, total: int | str, count: int
) -> str:
    try:
        text = template.format(page=page, pages=pages, total=total, count=count)
    except (KeyError, ValueError, IndexError, AttributeError, TypeError) as error:
        raise PaginationError("current_text", "has an invalid format") from error

    nonempty_string("current_text result", text)
    return text


@dataclass(frozen=True, kw_only=True)
class NavigationConfig:
    prefix: str = "aep1"
    previous_text: str = "←"
    next_text: str = "→"
    current_text: str = "{page}"
    layout: tuple[tuple[Button, ...], ...] = (("previous", "current", "next"),)
    ttl: float = 300
    max_entries: int = 1000
    stale_text: str = "Navigation expired. Open the list again."
    invalid_text: str = "Invalid navigation."

    def __post_init__(self) -> None:
        identifier("prefix", self.prefix)
        if len(self.prefix) > _MAX_PREFIX_LENGTH:
            raise PaginationError("prefix", "must contain at most 32 ASCII characters")

        seconds("ttl", self.ttl)

        positive_integer("max_entries", self.max_entries)
        if self.max_entries < _BUTTON_COUNT:
            raise PaginationError("max_entries", "must be at least 3")

        for name in ("previous_text", "next_text", "current_text", "stale_text", "invalid_text"):
            nonempty_string(name, getattr(self, name))

        _current_label(self.current_text, page=1, pages=1, total=1, count=1)

        require_instance("layout", self.layout, tuple)
        for row in self.layout:
            require_instance("layout row", row, tuple)
            if not row:
                raise PaginationError("layout", "must have non-empty rows")

        buttons = [button for row in self.layout for button in row]
        if len(buttons) != _BUTTON_COUNT or any(
            buttons.count(action) != 1 for action in ("previous", "current", "next")
        ):
            raise PaginationError("layout", "must contain previous, current and next exactly once")


@dataclass(frozen=True, kw_only=True)
class _Target:
    scope: str
    expires_at: float
    request: int | str | None


class Navigation(Generic[T]):
    def __init__(
        self,
        paginator: Paginator[T] | CursorPaginator[T],
        *,
        config: NavigationConfig | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._paginator = paginator
        self.config: NavigationConfig = config if config is not None else NavigationConfig()
        require_instance("config", self.config, NavigationConfig)

        self._clock = clock
        self._targets: OrderedDict[str, _Target] = OrderedDict()

    def _remove_expired(self, now: float) -> None:
        while self._targets:
            first = next(iter(self._targets.values()))
            if first.expires_at > now:
                break

            self._targets.popitem(last=False)

    def clear(self, *, scope: str | None = None) -> None:
        """Invalidate all references, or only those belonging to one scope."""
        if scope is None:
            self._targets.clear()
        else:
            references = [key for key, target in self._targets.items() if target.scope == scope]
            for key in references:
                del self._targets[key]

    def keyboard(self, page: Page[T] | CursorPage[T], *, scope: str) -> InlineKeyboardMarkup:
        nonempty_string("scope", scope)

        markup, requests = self._build_keyboard(page)
        self._publish_targets(requests, scope=scope)

        return markup

    def _build_keyboard(
        self, page: Page[T] | CursorPage[T]
    ) -> tuple[InlineKeyboardMarkup, dict[str, int | str | None]]:
        if isinstance(page, Page) != isinstance(self._paginator, Paginator):
            raise PaginationError("page", "does not match the paginator type")

        if isinstance(page, Page):
            previous, following = page.previous_page, page.next_page
            label = _current_label(
                self.config.current_text,
                page=page.number,
                pages=page.total_pages,
                total=page.total,
                count=len(page.items),
            )
        else:
            previous, following = page.previous_cursor, page.next_cursor
            label = _current_label(
                self.config.current_text, page="•", pages="?", total="?", count=len(page.items)
            )

        requests: dict[str, int | str | None] = {}
        buttons: dict[Button, InlineKeyboardButton] = {}

        actions: tuple[tuple[Button, str, int | str | None], ...] = (
            ("previous", self.config.previous_text, previous),
            ("current", label, None),
            ("next", self.config.next_text, following),
        )

        for action, text, request in actions:
            if request is None and action != "current":
                continue

            token = self.config.prefix + ":" + secrets.token_hex(12)
            requests[token] = request
            buttons[action] = InlineKeyboardButton(text=text, callback_data=token)

        rows = [
            [buttons[action] for action in row if action in buttons] for row in self.config.layout
        ]

        return InlineKeyboardMarkup(inline_keyboard=[row for row in rows if row]), requests

    def _publish_targets(self, requests: dict[str, int | str | None], *, scope: str) -> None:
        now = self._clock()
        self._remove_expired(now)
        self.clear(scope=scope)

        expires_at = now + self.config.ttl
        for token, request in requests.items():
            self._targets[token] = _Target(scope=scope, expires_at=expires_at, request=request)

        while len(self._targets) > self.config.max_entries:
            self._targets.popitem(last=False)

    async def resolve(self, data: str | None, *, scope: str) -> Page[T] | CursorPage[T] | None:
        nonempty_string("scope", scope)
        if data is None or not re.fullmatch(re.escape(self.config.prefix) + r":[0-9a-f]{24}", data):
            raise InvalidNavigationError

        now = self._clock()
        self._remove_expired(now)

        target = self._targets.get(data)
        if target is None or target.scope != scope or target.expires_at <= now:
            raise StaleNavigationError

        request = target.request
        if request is None:
            return None

        paginator = self._paginator
        page: Page[T] | CursorPage[T]

        try:
            if isinstance(paginator, Paginator):
                if not isinstance(request, int):
                    raise InvalidNavigationError

                page = await paginator.page(request)
            else:
                if not isinstance(request, str):
                    raise InvalidNavigationError

                page = await paginator.page(request)
        except (PageOutOfRangeError, InvalidCursorError) as error:
            raise StaleNavigationError from error

        if self._targets.get(data) is not target or target.expires_at <= self._clock():
            raise StaleNavigationError

        return page

    def matches(self, query: CallbackQuery) -> bool:
        """Accept this prefix in a router filter, including malformed callbacks."""
        return query.data is not None and query.data.startswith(self.config.prefix + ":")

    async def handle(
        self,
        query: CallbackQuery,
        *,
        scope: str,
        render: Callable[
            [CallbackQuery, Page[T] | CursorPage[T], InlineKeyboardMarkup], Awaitable[None]
        ],
    ) -> bool:
        """Answer owned callbacks and delegate message editing to the application."""
        if not self.matches(query):
            return False

        page = await self._answer(query, scope=scope)
        if page is not None:
            await render(query, page, self.keyboard(page, scope=scope))

        return True

    async def _answer(self, query: CallbackQuery, *, scope: str) -> Page[T] | CursorPage[T] | None:
        try:
            page = await self.resolve(query.data, scope=scope)
        except InvalidNavigationError:
            await query.answer(self.config.invalid_text)
        except StaleNavigationError:
            await query.answer(self.config.stale_text)
        else:
            await query.answer()
            return page

        return None


class TextPagination(Navigation[T]):
    """Send and update a user-bound text list through an aiogram router."""

    def __init__(
        self,
        paginator: Paginator[T] | CursorPaginator[T],
        *,
        render: Callable[[Page[T] | CursorPage[T]], str | Text],
        config: NavigationConfig | None = None,
    ) -> None:
        super().__init__(paginator, config=config)
        self._render = render
        self._locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()

        self.router: Router = Router(name=self.config.prefix)
        self.router.callback_query.register(self._navigate, self.matches)
        self.router.shutdown.register(self._shutdown)

    @staticmethod
    def _scope(bot: Bot, message: Message, user_id: int) -> str:
        return (
            f"{bot.id}:{message.chat.id}:{message.message_id}:{user_id}:"
            f"{message.business_connection_id or ''}"
        )

    def _content(self, page: Page[T] | CursorPage[T]) -> tuple[str, list[MessageEntity] | None]:
        content = self._render(page)
        return content.render() if isinstance(content, Text) else (content, None)

    async def answer(self, message: Message) -> Message:
        """Send the first page with controls usable by the sender of the incoming message."""
        user, bot = message.from_user, message.bot
        if user is None or bot is None:
            raise PaginationError("message", "must have a sender and be bound to a bot")

        page = await self._paginator.page()
        text, entities = self._content(page)
        markup, requests = self._build_keyboard(page)

        sent = await message.answer(text, entities=entities, parse_mode=None, reply_markup=markup)

        self._publish_targets(requests, scope=self._scope(bot, sent, user.id))

        return sent

    async def _navigate(self, query: CallbackQuery, bot: Bot) -> None:
        message = query.message
        if not isinstance(message, Message):
            await query.answer(self.config.stale_text)
            return

        scope = self._scope(bot, message, query.from_user.id)
        lock = self._locks.setdefault(scope, asyncio.Lock())

        async with lock:
            page = await self._answer(query, scope=scope)
            if page is None:
                return

            text, entities = self._content(page)
            markup, requests = self._build_keyboard(page)

            await message.edit_text(text, entities=entities, parse_mode=None, reply_markup=markup)
            # Keep the previous buttons usable if Telegram rejects the edit.
            self._publish_targets(requests, scope=scope)

    def _shutdown(self) -> None:
        self.clear()
