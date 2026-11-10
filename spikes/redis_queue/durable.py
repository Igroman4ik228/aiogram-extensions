"""Durable application policy, leaving task execution and transport to Taskiq."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import TYPE_CHECKING

from aiogram.exceptions import (
    TelegramAPIError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from payload import decode

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from aiogram import Bot
    from journal import DeliveryJournal


async def run_delivery(
    journal: DeliveryJournal,
    bot: Bot,
    job: str,
    generation: str,
    ack: Callable[[], Awaitable[None]],
    pause: Callable[[str], Awaitable[None]],
) -> str:
    owner = uuid.uuid4().hex
    claimed = await journal.claim(job, generation, owner)
    if claimed in {"terminal", "stale"}:
        state = await journal.snapshot(job)
        await ack()
        return state.get("status", "stale") if claimed == "terminal" else "stale"
    if claimed == "busy":
        return "busy"  # The original pending entry must not be acknowledged here.
    try:
        async with asyncio.TaskGroup() as tasks:
            heartbeat = tasks.create_task(_renew(journal, job, generation, owner))
            try:
                return await _owned_delivery(journal, bot, job, generation, owner, ack, pause)
            finally:
                heartbeat.cancel()
    finally:
        await journal.release(job, generation, owner)


async def _renew(journal: DeliveryJournal, job: str, generation: str, owner: str) -> None:
    while True:
        await asyncio.sleep(0.5)
        await journal.renew(job, generation, owner)


async def _owned_delivery(
    journal: DeliveryJournal,
    bot: Bot,
    job: str,
    generation: str,
    owner: str,
    ack: Callable[[], Awaitable[None]],
    pause: Callable[[str], Awaitable[None]],
) -> str:
    state = await journal.snapshot(job)
    method = decode(json.loads(state["wire"]))
    while True:
        attempt = await journal.begin_attempt(job, generation, owner)
        if attempt < 0:
            await pause("before_dead")
            await journal.deadletter(job, generation, owner)
            await pause("after_dead")
            await ack()
            return "dead"
        if attempt == 0:
            await asyncio.sleep(0.05)
            continue
        await pause("before_request")
        try:
            await bot(method)
        except (TelegramRetryAfter, TelegramNetworkError, TelegramServerError) as exc:
            delay = exc.retry_after * 1000 if isinstance(exc, TelegramRetryAfter) else 50
            await journal.fail(job, generation, owner, type(exc).__name__, retry_ms=delay)
            await pause("after_retry")
        except TelegramAPIError as exc:
            await journal.fail(job, generation, owner, type(exc).__name__, retry_ms=None)
        else:
            await pause("after_response")
            await journal.succeed(job, generation, owner)
            await pause("after_success")
            await ack()
            return "succeeded"
