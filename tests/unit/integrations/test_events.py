"""Callbacks observe events without breaking the triggering operation."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from aiogram_extensions.integrations import (
    BackendErrorEvent,
    EventDispatcher,
    LimitExceededEvent,
    TaskEvent,
)

if TYPE_CHECKING:
    from aiogram_extensions.integrations import Event


@pytest.mark.parametrize(
    "event",
    [
        TaskEvent(kind="task_enqueued", task_id="job-1", generation="gen-1"),
        TaskEvent(kind="task_started", task_id="job-1", generation="gen-1", attempt=1),
        TaskEvent(kind="task_completed", task_id="job-1", generation="gen-1", attempt=1),
        TaskEvent(kind="task_failed", task_id="job-1", generation="gen-1", attempt=3),
        TaskEvent(kind="retry_scheduled", task_id="job-1", generation="gen-1", retry_after=2),
        TaskEvent(kind="task_dlq", task_id="job-1", generation="gen-1", attempt=3),
        TaskEvent(kind="dlq_replayed", task_id="job-1", generation="gen-2"),
        LimitExceededEvent(direction="incoming", triggered_rules=("user",), retry_after=1),
        LimitExceededEvent(direction="outgoing", triggered_rules=("bot",), retry_after=2),
        BackendErrorEvent(component="redis", operation="connect"),
    ],
)
async def test_callbacks_receive_the_event_in_registration_order(event: Event) -> None:
    seen: list[tuple[str, Event]] = []

    async def first(event: Event) -> None:
        seen.append(("first", event))

    async def second(event: Event) -> None:
        seen.append(("second", event))

    await EventDispatcher((first, second)).emit(event)
    assert seen == [("first", event), ("second", event)]


async def test_callback_failure_is_logged_safely_and_remaining_callbacks_run(
    caplog: pytest.LogCaptureFixture,
) -> None:
    seen: list[Event] = []

    async def broken(event: Event) -> None:
        message = f"secret-token-sensitive-payload {event}"
        raise RuntimeError(message)

    async def observe(event: Event) -> None:
        seen.append(event)

    event = TaskEvent(kind="task_completed", task_id="private-id", generation="gen-1")
    await EventDispatcher((broken, observe)).emit(event)

    assert seen == [event]
    assert "callback failed" in caplog.text
    assert "secret-token" not in caplog.text
    assert "private-id" not in caplog.text
    assert caplog.records[0].name == "aiogram_extensions.integrations.events"


async def test_cancellation_propagates_and_stops_dispatch() -> None:
    seen: list[Event] = []

    async def cancel(_event: Event) -> None:
        raise asyncio.CancelledError

    async def observe(event: Event) -> None:
        seen.append(event)

    event = TaskEvent(kind="task_started", task_id="job-1", generation="gen-1")
    with pytest.raises(asyncio.CancelledError):
        await EventDispatcher((cancel, observe)).emit(event)
    assert seen == []
