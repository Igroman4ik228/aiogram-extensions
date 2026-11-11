"""Typed pagination over ordered collections and asynchronous data sources."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from aiogram_extensions._validation import positive_integer, require_instance

T_co = TypeVar("T_co", covariant=True)


class PaginationError(ValueError):
    """Invalid pagination input or an inconsistent source response."""

    def __init__(self, field: str, requirement: str) -> None:
        super().__init__(f"{field} {requirement}")


class PageOutOfRangeError(PaginationError):
    """The requested page no longer exists."""

    def __init__(self) -> None:
        super().__init__("page number", "is out of range")


@dataclass(frozen=True, kw_only=True)
class OffsetResult(Generic[T_co]):
    items: tuple[T_co, ...]
    total: int

    def __post_init__(self) -> None:
        require_instance("items", self.items, tuple)

        if type(self.total) is not int or self.total < 0:
            raise PaginationError("total", "must be a non-negative integer")


class OffsetSource(Protocol[T_co]):
    async def fetch(self, *, offset: int, limit: int) -> OffsetResult[T_co]: ...


class SequenceSource(Generic[T_co]):
    """A shallow snapshot retaining the supplied sequence's order."""

    def __init__(self, items: Sequence[T_co]) -> None:
        if isinstance(items, (str, bytes, bytearray)):
            raise PaginationError("source", "must be a sequence of items, not text or bytes")

        self._items = tuple(items)

    async def fetch(self, *, offset: int, limit: int) -> OffsetResult[T_co]:
        if type(offset) is not int or offset < 0:
            raise PaginationError("offset", "must be a non-negative integer")

        positive_integer("limit", limit)

        return OffsetResult(items=self._items[offset : offset + limit], total=len(self._items))


@dataclass(frozen=True, kw_only=True)
class Page(OffsetResult[T_co]):
    number: int
    limit: int

    def __post_init__(self) -> None:
        super().__post_init__()
        positive_integer("page number", self.number)
        positive_integer("limit", self.limit)

        if self.number > self.total_pages:
            raise PageOutOfRangeError

        if len(self.items) != min(self.limit, max(0, self.total - self.offset)):
            raise PaginationError("source", "returned items/total inconsistent with offset/limit")

    @property
    def offset(self) -> int:
        return (self.number - 1) * self.limit

    @property
    def total_pages(self) -> int:
        return max(1, (self.total + self.limit - 1) // self.limit)

    @property
    def previous_page(self) -> int | None:
        return self.number - 1 if self.number > 1 else None

    @property
    def next_page(self) -> int | None:
        return self.number + 1 if self.number < self.total_pages else None


class Paginator(Generic[T_co]):
    def __init__(self, source: Sequence[T_co] | OffsetSource[T_co], *, page_size: int = 10) -> None:
        positive_integer("page_size", page_size)

        self._source: OffsetSource[T_co] = (
            SequenceSource(source) if isinstance(source, Sequence) else source
        )
        self._page_size = page_size

    @property
    def page_size(self) -> int:
        return self._page_size

    async def page(self, number: int = 1) -> Page[T_co]:
        positive_integer("page number", number)

        offset = (number - 1) * self.page_size
        result = await self._source.fetch(offset=offset, limit=self.page_size)

        return Page(
            items=result.items,
            number=number,
            limit=self.page_size,
            total=result.total,
        )


def _validate_cursor(value: object) -> None:
    if value is not None and (not isinstance(value, str) or not value):
        raise PaginationError("cursor", "must be a non-empty string or None")


class InvalidCursorError(PaginationError):
    """A source rejected an expired, unknown or malformed cursor."""

    def __init__(self) -> None:
        super().__init__("cursor", "is expired, unknown or malformed")


@dataclass(frozen=True, kw_only=True)
class CursorResult(Generic[T_co]):
    items: tuple[T_co, ...]
    next_cursor: str | None = None
    previous_cursor: str | None = None

    def __post_init__(self) -> None:
        require_instance("items", self.items, tuple)

        _validate_cursor(self.next_cursor)
        _validate_cursor(self.previous_cursor)


@dataclass(frozen=True, kw_only=True)
class CursorPage(CursorResult[T_co]):
    cursor: str | None
    limit: int

    def __post_init__(self) -> None:
        super().__post_init__()
        _validate_cursor(self.cursor)
        positive_integer("limit", self.limit)

        if len(self.items) > self.limit:
            raise PaginationError("source", "returned more items than limit")

        if self.cursor is not None and self.cursor in (self.next_cursor, self.previous_cursor):
            raise PaginationError("source", "returned a cursor pointing to the current page")


class CursorSource(Protocol[T_co]):
    async def fetch(self, *, cursor: str | None, limit: int) -> CursorResult[T_co]: ...


class CursorPaginator(Generic[T_co]):
    def __init__(self, source: CursorSource[T_co], *, page_size: int = 10) -> None:
        positive_integer("page_size", page_size)

        self._source = source
        self._page_size = page_size

    @property
    def page_size(self) -> int:
        return self._page_size

    async def page(self, cursor: str | None = None) -> CursorPage[T_co]:
        _validate_cursor(cursor)

        result = await self._source.fetch(cursor=cursor, limit=self.page_size)

        return CursorPage(
            items=result.items,
            cursor=cursor,
            limit=self.page_size,
            next_cursor=result.next_cursor,
            previous_cursor=result.previous_cursor,
        )
