"""Pagination independent of Telegram UI."""

from aiogram_extensions.pagination.core import (
    CursorPage,
    CursorPaginator,
    CursorResult,
    CursorSource,
    InvalidCursorError,
    OffsetResult,
    OffsetSource,
    Page,
    PageOutOfRangeError,
    PaginationError,
    Paginator,
    SequenceSource,
)

__all__ = [
    "CursorPage",
    "CursorPaginator",
    "CursorResult",
    "CursorSource",
    "InvalidCursorError",
    "OffsetResult",
    "OffsetSource",
    "Page",
    "PageOutOfRangeError",
    "PaginationError",
    "Paginator",
    "SequenceSource",
]
