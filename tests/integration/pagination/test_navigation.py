"""Exercise navigation using real aiogram types without Telegram services."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.types import CallbackQuery, User

from aiogram_extensions.pagination import CursorPaginator, CursorResult, OffsetResult, Paginator
from aiogram_extensions.pagination import aiogram as ui


async def test_navigation_keyboard_resolves_the_next_page() -> None:
    paginator = Paginator(["a", "b", "c"], page_size=2)
    navigation = ui.Navigation(paginator)
    markup = navigation.keyboard(await paginator.page(), scope="chat:10/message:20/user:30")

    assert [[button.text for button in row] for row in markup.inline_keyboard] == [["1", "→"]]
    data = markup.inline_keyboard[0][-1].callback_data
    assert data is not None
    assert len(data.encode()) <= 64
    page = await navigation.resolve(data, scope="chat:10/message:20/user:30")
    assert page is not None
    assert page.items == ("c",)


async def test_navigation_expires_and_rejects_wrong_scope() -> None:
    now = [100.0]
    paginator = Paginator([1, 2], page_size=1)
    navigation = ui.Navigation(paginator, clock=lambda: now[0])
    markup = navigation.keyboard(await paginator.page(), scope="owner")
    data = markup.inline_keyboard[0][-1].callback_data
    assert data is not None
    with pytest.raises(ValueError, match="stale"):
        await navigation.resolve(data, scope="other")
    now[0] = 400
    with pytest.raises(ValueError, match="stale"):
        await navigation.resolve(data, scope="owner")


async def test_cursor_navigation_uses_short_references_and_custom_layout() -> None:
    cursor_value = "雪:/" * 1000

    class Source:
        async def fetch(self, *, cursor: str | None, limit: int) -> CursorResult[str]:  # noqa: ARG002
            if cursor is None:
                return CursorResult(items=("first",), next_cursor=cursor_value)
            assert cursor == cursor_value
            return CursorResult(items=("last",), previous_cursor="start")

    paginator = CursorPaginator(Source(), page_size=1)
    config = ui.NavigationConfig(
        prefix="catalog",
        next_text="Далее",
        current_text="{count} шт.",
        layout=(("current",), ("previous", "next")),
    )
    navigation = ui.Navigation(paginator, config=config)
    markup = navigation.keyboard(await paginator.page(), scope="catalog")
    assert [[button.text for button in row] for row in markup.inline_keyboard] == [
        ["1 шт."],
        ["Далее"],
    ]
    data = markup.inline_keyboard[1][0].callback_data
    assert data is not None
    assert data.startswith("catalog:")
    assert len(data.encode()) <= 64
    assert cursor_value not in data
    page = await navigation.resolve(data, scope="catalog")
    assert page is not None
    assert page.items == ("last",)


async def test_embedded_handler_answers_and_renders_next_page() -> None:
    paginator = Paginator(["a", "b"], page_size=1)
    navigation = ui.Navigation(paginator)
    markup = navigation.keyboard(await paginator.page(), scope="owner")
    data = markup.inline_keyboard[0][-1].callback_data
    query = CallbackQuery(
        id="query",
        from_user=User(id=1, is_bot=False, first_name="A"),
        chat_instance="chat",
        data=data,
    )
    bot = Bot("123456:synthetic")
    transport = AsyncMock(return_value=True)
    bot.session.make_request = transport
    render = AsyncMock()
    async with bot:
        assert navigation.matches(query)
        assert await navigation.handle(query.as_(bot), scope="owner", render=render)
    transport.assert_awaited_once()
    assert transport.call_args.args[1].__api_method__ == "answerCallbackQuery"
    render.assert_awaited_once()
    assert render.call_args.args[1].items == ("b",)


@pytest.mark.parametrize("data", [None, "", "other:abc", "aep1:bad", "aep1:" + "a" * 65, "雪" * 64])
async def test_invalid_callback_data_is_rejected(data: str | None) -> None:
    navigation = ui.Navigation(Paginator([1]))
    with pytest.raises(ui.InvalidNavigationError):
        await navigation.resolve(data, scope="owner")


async def test_unknown_reference_and_capacity_eviction_are_stale() -> None:
    paginator = Paginator([1, 2, 3], page_size=1)
    navigation = ui.Navigation(paginator, config=ui.NavigationConfig(max_entries=3))
    first = navigation.keyboard(await paginator.page(2), scope="old-owner")
    old = first.inline_keyboard[0][0].callback_data
    second = navigation.keyboard(await paginator.page(2), scope="owner")
    current = second.inline_keyboard[0][1].callback_data
    with pytest.raises(ui.StaleNavigationError):
        await navigation.resolve(old, scope="old-owner")
    with pytest.raises(ui.StaleNavigationError):
        await navigation.resolve("aep1:" + "0" * 24, scope="owner")
    assert await navigation.resolve(current, scope="owner") is None


async def test_clear_invalidates_only_the_requested_scope() -> None:
    paginator = Paginator([1])
    navigation = ui.Navigation(paginator)
    first = navigation.keyboard(await paginator.page(), scope="one")
    second = navigation.keyboard(await paginator.page(), scope="two")
    navigation.clear(scope="one")
    with pytest.raises(ui.StaleNavigationError):
        await navigation.resolve(first.inline_keyboard[0][0].callback_data, scope="one")
    assert await navigation.resolve(second.inline_keyboard[0][0].callback_data, scope="two") is None
    navigation.clear()
    with pytest.raises(ui.StaleNavigationError):
        await navigation.resolve(second.inline_keyboard[0][0].callback_data, scope="two")


async def test_deleted_page_is_reported_as_stale() -> None:
    items = [1, 2]

    class Source:
        async def fetch(self, *, offset: int, limit: int) -> OffsetResult[int]:
            return OffsetResult(items=tuple(items[offset : offset + limit]), total=len(items))

    paginator = Paginator(Source(), page_size=1)
    navigation = ui.Navigation(paginator)
    markup = navigation.keyboard(await paginator.page(), scope="owner")
    items.clear()
    with pytest.raises(ui.StaleNavigationError):
        await navigation.resolve(markup.inline_keyboard[0][-1].callback_data, scope="owner")


@pytest.mark.parametrize(
    ("settings", "error"),
    [
        ({"prefix": ""}, "prefix"),
        ({"prefix": "雪"}, "prefix"),
        ({"prefix": "a" * 33}, "prefix"),
        ({"prefix": "a:b"}, "prefix"),
        ({"ttl": 0}, "ttl"),
        ({"ttl": float("inf")}, "ttl"),
        ({"max_entries": True}, "max_entries"),
        ({"max_entries": 2}, "max_entries"),
        ({"current_text": "{unknown}"}, "current_text"),
        ({"current_text": "{"}, "current_text"),
        ({"current_text": "{page[0]}"}, "current_text"),
        ({"next_text": " "}, "next_text"),
        ({"layout": ()}, "layout"),
        ({"layout": (("next", "next", "current"),)}, "layout"),
        ({"layout": (("previous", "current", None),)}, "layout"),
    ],
)
def test_invalid_navigation_configuration_is_rejected(
    settings: dict[str, object], error: str
) -> None:
    options: dict[str, Any] = dict(settings)

    with pytest.raises(ValueError, match=error):
        ui.NavigationConfig(**options)


async def test_new_keyboard_invalidates_superseded_navigation_for_the_same_scope() -> None:
    paginator = Paginator([1, 2], page_size=1)
    navigation = ui.Navigation(paginator)
    first = navigation.keyboard(await paginator.page(), scope="owner")
    navigation.keyboard(await paginator.page(2), scope="owner")
    with pytest.raises(ui.StaleNavigationError):
        await navigation.resolve(first.inline_keyboard[0][-1].callback_data, scope="owner")


@pytest.mark.parametrize("invalidate", ["clear", "expire"])
async def test_navigation_rechecks_reference_after_source_read(invalidate: str) -> None:
    reading = asyncio.Event()
    resume = asyncio.Event()
    now = 100.0

    class Source:
        async def fetch(self, *, offset: int, limit: int) -> OffsetResult[int]:
            if offset:
                reading.set()
                await resume.wait()
            return OffsetResult(items=(1, 2)[offset : offset + limit], total=2)

    paginator = Paginator(Source(), page_size=1)
    navigation = ui.Navigation(paginator, clock=lambda: now)
    markup = navigation.keyboard(await paginator.page(), scope="owner")
    data = markup.inline_keyboard[0][-1].callback_data
    async with asyncio.timeout(5):
        task = asyncio.create_task(navigation.resolve(data, scope="owner"))
        try:
            await reading.wait()
            if invalidate == "clear":
                navigation.clear(scope="owner")
            else:
                now += 300
        finally:
            resume.set()
        with pytest.raises(ui.StaleNavigationError):
            await task


@pytest.mark.parametrize(
    ("data", "reply"),
    [
        (None, None),
        ("aep1:bad", "Invalid navigation."),
        ("aep1:" + "0" * 24, "Navigation expired. Open the list again."),
    ],
    ids=["current-page", "invalid-data", "unknown-reference"],
)
async def test_handler_answers_without_rendering(data: str | None, reply: str | None) -> None:
    paginator = Paginator([1])
    navigation = ui.Navigation(paginator)
    if data is None:
        markup = navigation.keyboard(await paginator.page(), scope="owner")
        data = markup.inline_keyboard[0][0].callback_data

    query = CallbackQuery(
        id="q", from_user=User(id=1, is_bot=False, first_name="A"), chat_instance="c", data=data
    )
    bot = Bot("123456:synthetic")
    transport = AsyncMock(return_value=True)
    bot.session.make_request = transport
    render = AsyncMock()

    async with bot:
        handled = await navigation.handle(query.as_(bot), scope="owner", render=render)

    assert handled
    render.assert_not_awaited()
    transport.assert_awaited_once()
    assert transport.call_args.args[1].text == reply


async def test_handler_leaves_callbacks_for_other_routers_unanswered() -> None:
    navigation = ui.Navigation(Paginator([1]))
    query = CallbackQuery(
        id="q",
        from_user=User(id=1, is_bot=False, first_name="Reader"),
        chat_instance="chat",
        data="other:callback",
    )
    render = AsyncMock()

    handled = await navigation.handle(query, scope="owner", render=render)

    assert not handled
    render.assert_not_awaited()
