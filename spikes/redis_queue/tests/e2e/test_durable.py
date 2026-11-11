"""Crash and concurrency guarantees at the durable producer/worker boundaries."""

import asyncio
from contextlib import suppress

import pytest
from aiogram.methods import SendMessage
from conftest import API, Harness, until
from journal import DeliveryJournal
from payload import encode
from redis_service import RedisService

pytestmark = pytest.mark.usefixtures("durable")


async def test_concurrent_producers_send_one_logical_job(harness: Harness, api: API) -> None:
    await harness.worker()
    wire = encode(SendMessage(chat_id=42, text="once"))

    await asyncio.gather(
        harness.producer("durable_enqueue", job="one", wire=wire),
        harness.producer("durable_enqueue", job="one", wire=wire),
    )
    await harness.completed("one")

    assert [call["fields"]["text"] for call in api.calls] == ["once"]
    journal = DeliveryJournal(harness.client, harness.prefix)
    assert (await journal.snapshot("one"))["status"] == "succeeded"
    assert await journal.outbox() == []


async def test_producer_crash_after_commit_preserves_outbox(harness: Harness, api: API) -> None:
    harness.env["SPIKE_DURABLE_PAUSE"] = "after_enqueue"
    producer = asyncio.create_task(
        harness.producer(
            "durable_enqueue",
            job="one",
            wire=encode(SendMessage(chat_id=42, text="outbox")),
        )
    )
    try:
        await harness.stage("one", "after_enqueue")
    finally:
        producer.cancel()  # Harness finally hard-kills this producer subprocess.
        with suppress(asyncio.CancelledError):
            await producer
    journal = DeliveryJournal(harness.client, harness.prefix)
    assert len(await journal.outbox()) == 1
    harness.env.pop("SPIKE_DURABLE_PAUSE")

    await harness.worker()
    await harness.completed("one")

    assert [call["fields"]["text"] for call in api.calls] == ["outbox"]
    assert await journal.outbox() == []


async def test_crash_after_publish_republishes_without_second_http_send(
    harness: Harness,
    api: API,
) -> None:
    harness.env["SPIKE_DURABLE_PAUSE"] = "after_publish"
    producer = asyncio.create_task(
        harness.producer(
            "durable_enqueue",
            job="one",
            wire=encode(SendMessage(chat_id=42, text="publish")),
        )
    )
    try:
        await harness.stage("one", "after_publish")
    finally:
        producer.cancel()
        with suppress(asyncio.CancelledError):
            await producer
    journal = DeliveryJournal(harness.client, harness.prefix)
    assert len(await journal.outbox()) == 1
    assert await harness.client.xlen(f"{harness.prefix}:queue") == 1
    harness.env.pop("SPIKE_DURABLE_PAUSE")
    await harness.worker(idle_ms=200)
    await harness.completed("one")

    async def acknowledged() -> bool:
        return await harness.pending() == 0

    await until(acknowledged)
    assert await harness.client.xlen(f"{harness.prefix}:queue") == 2
    assert [call["fields"]["text"] for call in api.calls] == ["publish"]
    assert await journal.outbox() == []


async def test_retry_budget_survives_worker_crash(harness: Harness, api: API) -> None:
    api.failures = [500] * 4
    harness.env["SPIKE_DURABLE_PAUSE"] = "after_retry"
    worker = await harness.worker(idle_ms=200)
    await harness.producer(
        "durable_enqueue",
        job="one",
        wire=encode(SendMessage(chat_id=42, text="budget")),
    )
    await harness.stage("one", "after_retry")
    await harness.kill(worker)
    harness.env.pop("SPIKE_DURABLE_PAUSE")
    await harness.worker(idle_ms=200)
    journal = DeliveryJournal(harness.client, harness.prefix)

    async def dead() -> bool:
        return (await journal.snapshot("one"))["status"] == "dead"

    await until(dead)

    assert len(api.calls) == 3
    assert (await journal.snapshot("one"))["attempts"] == "3"
    assert await harness.client.xlen(f"{harness.prefix}:durable-dlq") == 1


async def test_replay_survives_producer_crash_and_old_pending_delivery(
    harness: Harness,
    api: API,
) -> None:
    api.failures = [400]
    harness.env["SPIKE_DURABLE_PAUSE"] = "after_dead"
    worker = await harness.worker(idle_ms=200)
    await harness.producer(
        "durable_enqueue",
        job="one",
        wire=encode(SendMessage(chat_id=42, text="replay")),
    )
    await harness.stage("one", "after_dead")
    await harness.kill(worker)
    journal = DeliveryJournal(harness.client, harness.prefix)
    dead = await journal.snapshot("one")
    harness.env["SPIKE_DURABLE_PAUSE"] = "after_replay"
    producer = asyncio.create_task(
        harness.producer(
            "durable_replay",
            job="one",
            entry=dead["dead_entry"],
        )
    )
    try:
        await harness.stage("one", "after_replay")
    finally:
        producer.cancel()
        with suppress(asyncio.CancelledError):
            await producer
    queued = await journal.snapshot("one")
    assert queued["generation"] != dead["generation"]
    assert queued["status"] == "queued"
    assert await harness.client.xlen(f"{harness.prefix}:durable-dlq") == 0
    assert len(await journal.outbox()) == 1
    harness.env.pop("SPIKE_DURABLE_PAUSE")
    await harness.producer("durable_replay", job="one", entry=dead["dead_entry"])
    await harness.worker(idle_ms=200)
    await harness.completed("one")

    async def acknowledged() -> bool:
        return await harness.pending() == 0

    await until(acknowledged)
    assert len(api.calls) == 2
    assert (await journal.snapshot("one"))["generation"] == queued["generation"]


async def test_retry_after_deadline_survives_worker_crash(harness: Harness, api: API) -> None:
    api.failures = [429]
    api.retry_after = 5
    harness.env["SPIKE_DURABLE_PAUSE"] = "after_retry"
    worker = await harness.worker(idle_ms=200)
    await harness.producer(
        "durable_enqueue",
        job="one",
        wire=encode(SendMessage(chat_id=42, text="deadline")),
    )
    await harness.stage("one", "after_retry")
    await harness.kill(worker)
    harness.env.pop("SPIKE_DURABLE_PAUSE")
    await harness.worker(idle_ms=200)
    await harness.completed("one")

    assert len(api.calls) == 2
    assert api.calls[1]["time"] - api.calls[0]["time"] >= 5


@pytest.mark.parametrize(
    ("phase", "expected_calls", "expected_dead_letters"),
    [
        ("after_success", 1, 0),
        ("after_response", 2, 0),
        ("after_dead", 1, 1),
    ],
)
async def test_terminal_crash_windows(
    harness: Harness,
    api: API,
    phase: str,
    expected_calls: int,
    expected_dead_letters: int,
) -> None:
    if phase == "after_dead":
        api.failures = [400]
    harness.env["SPIKE_DURABLE_PAUSE"] = phase
    worker = await harness.worker(idle_ms=200)
    await harness.producer(
        "durable_enqueue",
        job="one",
        wire=encode(SendMessage(chat_id=42, text="terminal")),
    )
    await harness.stage("one", phase)
    await harness.kill(worker)

    harness.env.pop("SPIKE_DURABLE_PAUSE")
    await harness.worker(idle_ms=200)

    async def acknowledged() -> bool:
        return await harness.pending() == 0

    await until(acknowledged)

    assert len(api.calls) == expected_calls
    assert await harness.client.xlen(f"{harness.prefix}:durable-dlq") == expected_dead_letters


async def test_renewed_lease_prevents_parallel_http_send(harness: Harness, api: API) -> None:
    harness.env["SPIKE_DURABLE_PAUSE"] = "before_request"
    await harness.worker(idle_ms=200, max_tasks=1)
    await harness.producer(
        "durable_enqueue",
        job="one",
        wire=encode(SendMessage(chat_id=42, text="lease")),
    )
    await harness.stage("one", "before_request")
    harness.env.pop("SPIKE_DURABLE_PAUSE")
    await harness.worker(idle_ms=200, max_tasks=1)
    await asyncio.sleep(3.5)  # Beyond the original lease; heartbeat must have extended it.
    assert api.calls == []
    journal = DeliveryJournal(harness.client, harness.prefix)
    assert (await journal.snapshot("one"))["attempts"] == "1"

    await harness.release("one", "before_request")
    await harness.completed("one")

    assert [call["fields"]["text"] for call in api.calls] == ["lease"]


async def test_failed_dead_letter_write_does_not_resend_http(harness: Harness, api: API) -> None:
    api.failures = [400]
    harness.env["SPIKE_DURABLE_PAUSE"] = "before_dead"
    worker = await harness.worker(idle_ms=200)
    await harness.producer(
        "durable_enqueue",
        job="one",
        wire=encode(SendMessage(chat_id=42, text="dlq-write")),
    )
    await harness.stage("one", "before_dead")
    pending_before_failure = await harness.pending_ids()
    assert pending_before_failure
    await harness.client.set(f"{harness.prefix}:durable-dlq", "wrong-type")
    await harness.release("one", "before_dead")
    await asyncio.sleep(0.3)
    assert pending_before_failure <= await harness.pending_ids()
    await harness.kill(worker)
    await harness.client.delete(f"{harness.prefix}:durable-dlq")
    harness.env.pop("SPIKE_DURABLE_PAUSE")
    await harness.worker(idle_ms=200)

    async def acknowledged() -> bool:
        return await harness.pending() == 0

    await until(acknowledged)

    assert len(api.calls) == 1
    assert await harness.client.xlen(f"{harness.prefix}:durable-dlq") == 1


async def test_aof_recovers_after_worker_and_redis_hard_kill(
    harness: Harness,
    api: API,
    redis_server: RedisService,
) -> None:
    harness.env["SPIKE_DURABLE_PAUSE"] = "before_request"
    worker = await harness.worker(idle_ms=200)
    await harness.producer(
        "durable_enqueue",
        job="one",
        wire=encode(SendMessage(chat_id=42, text="aof")),
    )
    await harness.stage("one", "before_request")
    pending_before_crash = await harness.pending_ids()
    assert pending_before_crash
    await harness.kill(worker)
    await asyncio.to_thread(redis_server.crash_restart)
    await harness.client.connection_pool.disconnect()  # Discard the observer's pre-crash socket.
    assert pending_before_crash <= await harness.pending_ids()
    journal = DeliveryJournal(harness.client, harness.prefix)
    assert (await journal.snapshot("one"))["attempts"] == "1"
    harness.env.pop("SPIKE_DURABLE_PAUSE")
    await harness.worker(idle_ms=200)
    await harness.completed("one")

    assert [call["fields"]["text"] for call in api.calls] == ["aof"]
    assert (await journal.snapshot("one"))["attempts"] == "2"


async def test_backend_outage_cancels_owned_work_before_http(
    harness: Harness,
    api: API,
    redis_server: RedisService,
) -> None:
    harness.env["SPIKE_DURABLE_PAUSE"] = "before_request"
    worker = await harness.worker(idle_ms=200)
    await harness.producer(
        "durable_enqueue",
        job="one",
        wire=encode(SendMessage(chat_id=42, text="outage")),
    )
    await harness.stage("one", "before_request")
    await asyncio.to_thread(redis_server.pause)
    try:
        await asyncio.sleep(3.5)
        assert api.calls == []
    finally:
        await asyncio.to_thread(redis_server.unpause)
        await harness.kill(worker)
    harness.env.pop("SPIKE_DURABLE_PAUSE")
    await harness.worker(idle_ms=200)
    await harness.completed("one")

    assert [call["fields"]["text"] for call in api.calls] == ["outage"]
