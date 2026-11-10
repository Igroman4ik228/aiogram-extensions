"""Recovery contract a candidate transport must satisfy without fresh traffic."""

import pytest
from aiogram.methods import SendMessage
from conftest import API, Harness
from payload import encode


@pytest.mark.usefixtures("recovery_candidate")
async def test_idle_worker_recovers_after_hard_kill(harness: Harness, api: API) -> None:
    await harness.producer(
        wire=encode(SendMessage(chat_id=42, text="recover")), job="recover", pause="before"
    )
    worker = await harness.worker(idle_ms=200)
    await harness.stage("recover", "before")
    await harness.kill(worker)
    assert await harness.pending() == 1
    await harness.release("recover", "before")

    await harness.worker(idle_ms=200)
    await harness.completed("recover")

    assert [call["fields"]["text"] for call in api.calls] == ["recover"]
    assert await harness.pending() == 0


@pytest.mark.usefixtures("recovery_candidate")
async def test_idle_recovery_drains_multiple_reclaim_batches(harness: Harness, api: API) -> None:
    harness.env["SPIKE_RECLAIM_BATCH"] = "2"
    jobs = [f"batch-{index}" for index in range(5)]
    for job in jobs:
        await harness.producer(
            wire=encode(SendMessage(chat_id=42, text=job)), job=job, pause="before"
        )
    worker = await harness.worker(max_tasks=8)
    for job in jobs:
        await harness.stage(job, "before")
    await harness.kill(worker)
    assert await harness.pending() == 5
    for job in jobs:
        await harness.release(job, "before")

    await harness.worker(idle_ms=200)
    for job in jobs:
        await harness.completed(job)

    assert sorted(call["fields"]["text"] for call in api.calls) == jobs
    assert await harness.pending() == 0
