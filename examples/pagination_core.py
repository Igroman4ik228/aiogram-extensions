"""Ordered collection and opaque cursor pagination without Telegram I/O."""

import asyncio

from aiogram_extensions.pagination import (
    CursorPaginator,
    CursorResult,
    InvalidCursorError,
    PaginationError,
    Paginator,
)
from aiogram_extensions.utilities import truncate_text

PAGE_SIZE = 2


class CatalogSource:
    """Two ordered keyset pages; a real adapter would query a database."""

    async def fetch(self, *, cursor: str | None, limit: int) -> CursorResult[str]:
        if limit != PAGE_SIZE:
            raise PaginationError("limit", "must match the example page size")
        if cursor in (None, "start"):
            return CursorResult(items=("Apple", "Banana"), next_cursor="after:banana")
        if cursor == "after:banana":
            return CursorResult(items=("Cherry",), previous_cursor="start")
        raise InvalidCursorError


async def main() -> None:
    paginator = Paginator(["Apple", "Banana", "Cherry"], page_size=PAGE_SIZE)
    page = await paginator.page(2)
    print(f"Offset page: {page.number}/{page.total_pages}, items: {', '.join(page.items)}")
    cursors = CursorPaginator(CatalogSource(), page_size=PAGE_SIZE)
    first = await cursors.page()
    last = await cursors.page(first.next_cursor)
    print(f"Cursor page: {', '.join(last.items)}")
    print(f"Preview: {truncate_text('A long catalog title', max_units=10, suffix='...')}")


if __name__ == "__main__":
    asyncio.run(main())
