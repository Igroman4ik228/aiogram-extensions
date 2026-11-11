"""Observe delivery at the real process, Redis and HTTP boundaries."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from aiogram.methods import (
    SendAudio,
    SendContact,
    SendDocument,
    SendLocation,
    SendMediaGroup,
    SendMessage,
    SendPhoto,
    SendVideo,
    TelegramMethod,
)
from aiogram.types import FSInputFile, InputMediaPhoto
from conftest import API, Harness, until
from payload import encode


async def test_producer_worker_and_http_delivery(harness: Harness, api: API) -> None:
    await harness.producer(wire=encode(SendMessage(chat_id=42, text="hello")), job="hello")

    assert await harness.values("opened") == []

    await harness.worker()
    await harness.completed("hello")

    assert [(call["method"], call["fields"]["text"]) for call in api.calls] == [
        ("sendMessage", "hello"),
    ]
    assert await harness.pending() == 0


async def test_required_methods_and_media_references(
    harness: Harness,
    api: API,
    tmp_path: Path,
) -> None:
    path = tmp_path / "media.bin"
    path.write_bytes(b"shared-file-contents")

    methods: list[TelegramMethod[Any]] = [
        SendMessage(chat_id=42, text="text"),
        SendLocation(chat_id=42, latitude=55.75, longitude=37.62),
        SendContact(chat_id=42, phone_number="+10000000000", first_name="Test"),
    ]
    for reference in ("test-file-id", "https://example.invalid/media", FSInputFile(path)):
        methods.extend(
            [
                SendPhoto(chat_id=42, photo=reference),
                SendDocument(chat_id=42, document=reference),
                SendVideo(chat_id=42, video=reference),
                SendAudio(chat_id=42, audio=reference),
                SendMediaGroup(
                    chat_id=42,
                    media=[
                        InputMediaPhoto(media=reference),
                        InputMediaPhoto(media=reference),
                    ],
                ),
            ]
        )
    task = "durable_enqueue" if harness.env.get("SPIKE_DURABLE") == "1" else "deliver"
    await harness.worker()
    for index, method in enumerate(methods):
        await harness.producer(task, wire=encode(method), job=str(index))
        await harness.completed(str(index))

    assert [call["method"] for call in api.calls] == [
        "sendMessage",
        "sendLocation",
        "sendContact",
        *(["sendPhoto", "sendDocument", "sendVideo", "sendAudio", "sendMediaGroup"] * 3),
    ]
    assert api.calls[1]["fields"]["latitude"] == "55.75"
    assert api.calls[2]["fields"]["first_name"] == "Test"

    for start, expected in ((3, "test-file-id"), (8, "https://example.invalid/media")):
        for offset, field in enumerate(("photo", "document", "video", "audio")):
            assert api.calls[start + offset]["fields"][field] == expected
        assert [item["media"] for item in json.loads(api.calls[start + 4]["fields"]["media"])] == [
            expected,
            expected,
        ]
    for call in api.calls[13:]:
        assert b"shared-file-contents" in call["fields"].values()

    assert await harness.pending() == 0


@pytest.mark.parametrize(("phase", "deliveries"), [("before", 1), ("during", 2), ("after", 2)])
async def test_hard_kill_delivery_and_idle_recovery(
    harness: Harness,
    api: API,
    phase: str,
    deliveries: int,
) -> None:
    if phase == "during":
        api.hold = asyncio.Event()
    await harness.producer(
        wire=encode(SendMessage(chat_id=42, text="crash")),
        job="crash",
        pause=phase if phase != "during" else "",
    )
    worker = await harness.worker(idle_ms=200)
    if phase == "during":

        async def called() -> bool:
            return bool(api.calls)

        await until(called)
    else:
        await harness.stage("crash", phase)
    await harness.kill(worker)
    calls_before_restart = len(api.calls)
    assert await harness.pending() == 1

    if api.hold is not None:
        api.hold.set()
        api.hold = None
    else:
        await harness.release("crash", phase)

    await harness.worker(idle_ms=200)
    if not harness.candidate:
        await asyncio.sleep(0.6)  # Three idle intervals and six XREADGROUP block intervals.
        assert len(api.calls) == calls_before_restart
        assert await harness.pending() == 1
        await harness.producer(wire=encode(SendMessage(chat_id=42, text="trigger")), job="trigger")
        await harness.completed("trigger")
    await harness.completed("crash")

    assert sum(call["fields"].get("text") == "crash" for call in api.calls) == deliveries
    assert await harness.pending() == 0


async def test_live_long_task_can_be_claimed_by_another_worker(harness: Harness, api: API) -> None:
    await harness.producer(
        wire=encode(SendMessage(chat_id=42, text="long")),
        job="long",
        pause="before",
    )
    first = await harness.worker(idle_ms=200, max_tasks=1)
    await harness.stage("long", "before")
    # Saturated worker one cannot read new traffic; its task is still alive.
    await harness.worker(idle_ms=200, max_tasks=1)
    await harness.producer(wire=encode(SendMessage(chat_id=42, text="trigger")), job="trigger")

    async def duplicated() -> bool:
        return len(set(await harness.values("owners:long"))) >= 2

    await until(duplicated)
    assert first.poll() is None
    await harness.release("long", "before")
    await harness.release("long", "before")

    async def sent_twice() -> bool:
        return sum(call["fields"].get("text") == "long" for call in api.calls) >= 2

    await until(sent_twice)


async def test_task_below_idle_threshold_is_not_claimed_by_another_worker(
    harness: Harness,
    api: API,
) -> None:
    await harness.producer(
        wire=encode(SendMessage(chat_id=42, text="bounded")), job="bounded", pause="before"
    )
    await harness.worker(idle_ms=60000, max_tasks=1)
    await harness.stage("bounded", "before")
    await harness.worker(idle_ms=60000, max_tasks=1)
    await harness.producer(wire=encode(SendMessage(chat_id=42, text="trigger")), job="trigger")
    await harness.completed("trigger")

    assert len(await harness.values("owners:bounded")) == 1
    assert [call["fields"]["text"] for call in api.calls] == ["trigger"]

    await harness.release("bounded", "before")
    await harness.completed("bounded")

    assert [call["fields"]["text"] for call in api.calls] == ["trigger", "bounded"]
    assert await harness.pending() == 0


@pytest.mark.parametrize(
    "failure", [0, 429, 500], ids=["disconnect", "retry-after", "server-error"]
)
async def test_transient_retry_and_retry_after(harness: Harness, api: API, failure: int) -> None:
    api.failures = [failure]
    await harness.worker()
    await harness.producer(wire=encode(SendMessage(chat_id=42, text="retry")), job="retry")
    await harness.completed("retry")

    assert len(api.calls) == 2
    if failure == 429:
        assert api.calls[1]["time"] - api.calls[0]["time"] >= 1
    assert await harness.pending() == 0
    assert await harness.client.xlen(f"{harness.prefix}:dlq") == 0


@pytest.mark.parametrize(
    ("failures", "attempts", "error"),
    [
        pytest.param([400], 1, "TelegramBadRequest", id="permanent-error"),
        pytest.param([500, 500, 500], 3, "TelegramServerError", id="retry-budget-exhausted"),
    ],
)
async def test_terminal_failure_dlq_and_explicit_replay(
    harness: Harness,
    api: API,
    failures: list[int],
    attempts: int,
    error: str,
) -> None:
    api.failures = failures.copy()
    await harness.worker()
    await harness.producer(wire=encode(SendMessage(chat_id=42, text="dlq")), job="dlq")

    async def dead_letter() -> Any:
        return await harness.client.xrange(f"{harness.prefix}:dlq")

    entries = await until(dead_letter)

    entry_id, fields = entries[0]
    assert len(api.calls) == attempts
    assert fields["attempts"] == str(attempts)
    assert fields["error"] == error

    async def acknowledged() -> bool:
        return await harness.pending() == 0

    await until(acknowledged)

    # Deliberately non-atomic experiment: enqueue before deleting the DLQ record.
    await harness.producer(wire=json.loads(fields["wire"]), job="replay")
    await harness.completed("replay")
    await harness.client.xdel(f"{harness.prefix}:dlq", entry_id)

    assert len(api.calls) == attempts + 1
    assert await harness.client.xlen(f"{harness.prefix}:dlq") == 0


async def test_failed_dlq_write_keeps_original_pending(harness: Harness, api: API) -> None:
    await harness.client.set(f"{harness.prefix}:dlq", "wrong-type")
    api.failures = [400]
    await harness.worker()
    await harness.producer(wire=encode(SendMessage(chat_id=42, text="fail-closed")), job="fail")

    async def called() -> bool:
        return bool(api.calls)

    await until(called)
    await asyncio.sleep(0.2)

    assert await harness.pending() == 1
    assert await harness.values("completed") == []


async def test_stock_retry_is_immediate_and_has_no_dlq(harness: Harness, api: API) -> None:
    api.failures = [429, 429, 429]
    await harness.worker()
    await harness.producer("stock_retry", wire=encode(SendMessage(chat_id=42, text="stock")))

    async def exhausted() -> bool:
        return len(api.calls) >= 3 and await harness.pending() == 0

    await until(exhausted)

    assert len(api.calls) == 3
    assert api.calls[-1]["time"] - api.calls[0]["time"] < 1
    assert await harness.client.xlen(f"{harness.prefix}:dlq") == 0


async def test_worker_hooks_close_bot_and_independent_redis_client(harness: Harness) -> None:
    worker = await harness.worker()
    opened = await harness.values("opened")
    await harness.producer("finish")

    async def closed() -> bool:
        return opened[0] in await harness.values("closed")

    await until(closed)
    assert await harness.client.ping()  # pyright: ignore[reportUnknownMemberType]
    await harness.kill(worker)
