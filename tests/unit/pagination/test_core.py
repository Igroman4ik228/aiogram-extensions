"""Pagination behavior at the public source and page boundaries."""

from typing import Any

import pytest

from aiogram_extensions.pagination import (
    CursorPage,
    CursorPaginator,
    CursorResult,
    InvalidCursorError,
    OffsetResult,
    Page,
    PageOutOfRangeError,
    Paginator,
    SequenceSource,
)


async def test_collection_pages_preserve_order_and_report_navigation() -> None:
    items = ["a", "b", "c", "d", "e"]
    paginator = Paginator(items, page_size=2)
    items.append("later")

    page = await paginator.page(2)

    assert page.items == ("c", "d")
    assert page.number == 2
    assert page.offset == 2
    assert page.limit == 2
    assert page.total == 5
    assert page.total_pages == 3
    assert page.previous_page == 1
    assert page.next_page == 3


@pytest.mark.parametrize("number", [0, -1, True, 1.5, 4])
async def test_invalid_page_is_rejected(number: int) -> None:
    with pytest.raises(ValueError, match="page"):
        await Paginator([1, 2, 3], page_size=1).page(number)


async def test_async_source_receives_offset_limit_and_reflects_changes() -> None:
    calls: list[tuple[int, int]] = []
    items = ["a", "b", "c"]

    class Source:
        async def fetch(self, *, offset: int, limit: int) -> OffsetResult[str]:
            calls.append((offset, limit))
            return OffsetResult(items=tuple(items[offset : offset + limit]), total=len(items))

    paginator = Paginator(Source(), page_size=2)

    page = await paginator.page(2)

    assert page.items == ("c",)
    assert (page.previous_page, page.next_page, page.total_pages) == (1, None, 2)
    assert calls == [(2, 2)]

    items.clear()

    with pytest.raises(PageOutOfRangeError):
        await paginator.page(2)


async def test_cursor_pages_keep_opaque_bidirectional_tokens() -> None:
    calls: list[tuple[str | None, int]] = []

    class Source:
        async def fetch(self, *, cursor: str | None, limit: int) -> CursorResult[str]:
            calls.append((cursor, limit))
            if cursor is None or cursor == "start":
                return CursorResult(items=("a", "b"), next_cursor="opaque/after:b?雪")
            return CursorResult(items=("c",), previous_cursor="start")

    paginator = CursorPaginator(Source(), page_size=2)

    first = await paginator.page()
    last = await paginator.page(first.next_cursor)
    previous = await paginator.page(last.previous_cursor)

    assert first.items == previous.items == ("a", "b")
    assert last.items == ("c",)
    assert last.cursor == "opaque/after:b?雪"
    assert last.limit == 2
    assert first.previous_cursor is last.next_cursor is None
    assert calls == [(None, 2), ("opaque/after:b?雪", 2), ("start", 2)]


async def test_empty_collection_has_one_page_without_neighbors() -> None:
    items: tuple[str, ...] = ()

    page = await Paginator(items, page_size=1).page()

    assert page.items == ()
    assert page.total == 0
    assert page.total_pages == 1
    assert page.previous_page is None
    assert page.next_page is None


async def test_first_and_last_pages_have_only_one_neighbor() -> None:
    paginator = Paginator(["first", "last"], page_size=1)

    first = await paginator.page()
    last = await paginator.page(2)

    assert first.items == ("first",)
    assert first.previous_page is None
    assert first.next_page == 2
    assert last.items == ("last",)
    assert last.previous_page == 1
    assert last.next_page is None


async def test_single_item_fits_on_one_page() -> None:
    page = await Paginator(["only"], page_size=1).page()

    assert page.items == ("only",)
    assert page.total_pages == 1
    assert page.previous_page is None
    assert page.next_page is None


@pytest.mark.parametrize("size", [0, -1, True, 1.5])
def test_paginator_rejects_invalid_page_size(size: int) -> None:
    with pytest.raises(ValueError, match="page_size"):
        Paginator([1], page_size=size)


async def test_source_failure_is_not_hidden() -> None:
    class Source:
        async def fetch(self, *, offset: int, limit: int) -> OffsetResult[int]:  # noqa: ARG002
            raise OSError("offline")

    with pytest.raises(OSError, match="offline"):
        await Paginator(Source()).page()


@pytest.mark.parametrize("total", [-1, True, 1.5])
def test_offset_result_rejects_invalid_total(total: int) -> None:
    with pytest.raises(ValueError, match="total"):
        OffsetResult(items=(), total=total)


@pytest.mark.parametrize("items", [(1,), (1, 2, 3)])
async def test_offset_source_must_return_a_consistent_slice(items: tuple[int, ...]) -> None:
    class Source:
        async def fetch(self, *, offset: int, limit: int) -> OffsetResult[int]:  # noqa: ARG002
            return OffsetResult(items=items, total=3)

    with pytest.raises(ValueError, match="inconsistent"):
        await Paginator(Source(), page_size=2).page()


@pytest.mark.parametrize("cursor", ["", 1, False])
async def test_invalid_cursor_is_rejected_before_source_access(cursor: str) -> None:
    class Source:
        async def fetch(self, *, cursor: str | None, limit: int) -> CursorResult[int]:  # noqa: ARG002
            pytest.fail("invalid cursor reached the source")

    with pytest.raises(ValueError, match="cursor"):
        await CursorPaginator(Source()).page(cursor)


async def test_sequence_source_accepts_direct_offset_limit() -> None:
    source = SequenceSource(["a", "b", "c", "d"])

    result = await source.fetch(offset=1, limit=2)

    assert result.items == ("b", "c")
    assert result.total == 4


@pytest.mark.parametrize(
    ("settings", "error"),
    [
        ({"number": 0}, "page number"),
        ({"limit": 0}, "limit"),
        ({"total": -1}, "total"),
        ({"items": []}, "items"),
        ({"total": 0}, "inconsistent"),
    ],
)
def test_page_rejects_inconsistent_metadata(settings: dict[str, object], error: str) -> None:
    options: dict[str, Any] = {"items": (1,), "number": 1, "limit": 2, "total": 1}
    options.update(settings)

    with pytest.raises(ValueError, match=error):
        Page(**options)


@pytest.mark.parametrize(
    ("result", "error"),
    [
        (CursorResult(items=(1, 2)), "more items than limit"),
        (CursorResult(items=(1,), next_cursor="opaque"), "current page"),
    ],
    ids=["too-many-items", "self-reference"],
)
async def test_cursor_paginator_rejects_inconsistent_source_results(
    result: CursorResult[int], error: str
) -> None:
    class Source:
        async def fetch(self, *, cursor: str | None, limit: int) -> CursorResult[int]:  # noqa: ARG002
            return result

    paginator = CursorPaginator(Source(), page_size=1)

    with pytest.raises(ValueError, match=error):
        await paginator.page("opaque")


async def test_expired_cursor_error_propagates() -> None:
    class Source:
        async def fetch(self, *, cursor: str | None, limit: int) -> CursorResult[int]:  # noqa: ARG002
            raise InvalidCursorError

    with pytest.raises(InvalidCursorError):
        await CursorPaginator(Source()).page("expired")


async def test_empty_cursor_page_has_no_neighbors() -> None:
    class Source:
        async def fetch(self, *, cursor: str | None, limit: int) -> CursorResult[int]:  # noqa: ARG002
            return CursorResult(items=())

    page = await CursorPaginator(Source()).page()

    assert page.items == ()
    assert page.previous_cursor is None
    assert page.next_cursor is None


@pytest.mark.parametrize("offset", [-1, True, 1.5])
async def test_sequence_source_rejects_invalid_offset(offset: int) -> None:
    with pytest.raises(ValueError, match="offset"):
        await SequenceSource([1]).fetch(offset=offset, limit=1)


@pytest.mark.parametrize("items", ["text", b"bytes", bytearray(b"bytes")])
def test_text_is_not_an_item_collection(items: str | bytes | bytearray) -> None:
    with pytest.raises(ValueError, match="source"):
        Paginator(items)


@pytest.mark.parametrize(
    ("settings", "error"),
    [
        ({"limit": 0}, "limit must be"),
        ({"limit": 1}, "more items than limit"),
        ({"cursor": ""}, "cursor must be"),
        ({"next_cursor": "current"}, "current page"),
    ],
)
def test_cursor_page_rejects_invalid_metadata(settings: dict[str, object], error: str) -> None:
    options: dict[str, Any] = {"items": (1, 2), "cursor": "current", "limit": 2}
    options.update(settings)

    with pytest.raises(ValueError, match=error):
        CursorPage(**options)
