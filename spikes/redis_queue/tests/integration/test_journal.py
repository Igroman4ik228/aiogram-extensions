"""Check state transitions against isolated real Redis."""

import asyncio
import re
import uuid
from typing import Any

import pytest
from aiogram.methods import SendMessage
from journal import DeliveryJournal
from payload import encode
from redis.asyncio import Redis
from redis.exceptions import ResponseError


async def test_enqueue_deduplicates_one_logical_job(journal: DeliveryJournal) -> None:
    wire = encode(SendMessage(chat_id=42, text="once"))

    first, generation = await journal.enqueue("one", wire)
    second, duplicate_generation = await journal.enqueue("one", wire)

    assert first is True
    assert second is False
    assert generation == duplicate_generation
    state = await journal.snapshot("one")
    assert state["status"] == "queued"
    assert state["attempts"] == "0"


async def test_only_current_owner_can_release_delivery(journal: DeliveryJournal) -> None:
    _, generation = await journal.enqueue("one", encode(SendMessage(chat_id=42, text="once")))

    assert await journal.claim("one", generation, "first") == "acquired"
    assert await journal.claim("one", generation, "second") == "busy"
    assert await journal.release("one", generation, "second") is False
    assert await journal.claim("one", generation, "second") == "busy"
    assert await journal.release("one", generation, "first") is True
    assert await journal.claim("one", generation, "second") == "acquired"


async def test_attempt_and_retry_deadline_survive_new_journal(journal: DeliveryJournal) -> None:
    _, generation = await journal.enqueue("one", encode(SendMessage(chat_id=42, text="retry")))
    assert await journal.claim("one", generation, "first") == "acquired"
    assert await journal.begin_attempt("one", generation, "first") == 1
    await journal.fail("one", generation, "first", "TelegramRetryAfter", retry_ms=300)
    await journal.release("one", generation, "first")

    restarted = DeliveryJournal(journal.client, journal.prefix)
    assert await restarted.claim("one", generation, "second") == "acquired"
    assert (await restarted.snapshot("one"))["attempts"] == "1"
    assert await restarted.begin_attempt("one", generation, "second") == 0
    await asyncio.sleep(0.35)
    assert await restarted.begin_attempt("one", generation, "second") == 2


async def test_attempt_budget_and_dead_letter_are_durable_and_idempotent(
    journal: DeliveryJournal,
) -> None:
    _, generation = await journal.enqueue("one", encode(SendMessage(chat_id=42, text="fail")))
    assert await journal.claim("one", generation, "owner") == "acquired"
    for attempt in range(1, 4):
        assert await journal.begin_attempt("one", generation, "owner") == attempt
        await journal.fail("one", generation, "owner", "TelegramServerError", retry_ms=0)
    assert await journal.begin_attempt("one", generation, "owner") == -1

    first = await journal.deadletter("one", generation, "owner")
    second = await journal.deadletter("one", generation, "owner")

    assert first == second
    assert await journal.client.xlen(f"{journal.prefix}:durable-dlq") == 1
    state = await journal.snapshot("one")
    assert state["status"] == "dead"
    assert state["attempts"] == "3"


async def test_concurrent_replay_creates_one_new_generation(journal: DeliveryJournal) -> None:
    _, generation = await journal.enqueue("one", encode(SendMessage(chat_id=42, text="replay")))
    await journal.claim("one", generation, "owner")
    await journal.begin_attempt("one", generation, "owner")
    await journal.fail("one", generation, "owner", "TelegramBadRequest", retry_ms=None)
    entry = await journal.deadletter("one", generation, "owner")

    first, second = await asyncio.gather(journal.replay("one", entry), journal.replay("one", entry))

    assert first[0] + second[0] == 1
    assert first[1] == second[1]
    assert first[1] != generation
    state = await journal.snapshot("one")
    assert state["status"] == "queued"
    assert state["attempts"] == "0"
    assert await journal.client.zcard(f"{journal.prefix}:outbox") == 1
    assert await journal.client.xlen(f"{journal.prefix}:durable-dlq") == 0
    assert await journal.claim("one", generation, "stale-worker") == "stale"


async def test_success_remains_terminal_after_owner_release(journal: DeliveryJournal) -> None:
    _, generation = await journal.enqueue("one", encode(SendMessage(chat_id=42, text="success")))
    await journal.claim("one", generation, "owner")
    await journal.renew("one", generation, "owner")
    await journal.begin_attempt("one", generation, "owner")
    await journal.succeed("one", generation, "owner")
    await journal.release("one", generation, "owner")

    assert await journal.claim("one", generation, "other") == "terminal"
    assert (await journal.snapshot("one"))["status"] == "succeeded"


async def test_retention_cleanup_preserves_active_jobs(journal: DeliveryJournal) -> None:
    short = DeliveryJournal(journal.client, journal.prefix, retention_ms=100)
    wire = encode(SendMessage(chat_id=42, text="retention"))
    _, generation = await short.enqueue("dead", wire)
    await short.enqueue("active", wire)
    await short.claim("dead", generation, "owner")
    await short.begin_attempt("dead", generation, "owner")
    await short.fail("dead", generation, "owner", "TelegramBadRequest", retry_ms=None)
    await short.deadletter("dead", generation, "owner")
    await asyncio.sleep(0.15)

    assert await short.cleanup() == 1
    assert await journal.snapshot("dead") == {}
    assert (await journal.snapshot("active"))["status"] == "queued"
    assert await journal.client.xlen(f"{journal.prefix}:durable-dlq") == 0
    _, new_generation = await journal.enqueue("dead", wire)
    assert new_generation != generation
    assert await journal.claim("dead", generation, "old") == "stale"


async def test_enqueue_rejects_conflicts_and_oversize_before_new_state(
    journal: DeliveryJournal,
) -> None:
    _, generation = await journal.enqueue("one", encode(SendMessage(chat_id=42, text="original")))
    with pytest.raises(ResponseError, match="Job payload conflict"):
        await journal.enqueue("one", encode(SendMessage(chat_id=42, text="different")))
    with pytest.raises(ValueError, match="262144"):
        await journal.enqueue("large", encode(SendMessage(chat_id=42, text="x" * 262144)))
    assert (await journal.snapshot("one"))["generation"] == generation
    assert await journal.snapshot("large") == {}
    assert len(await journal.outbox()) == 1


@pytest.mark.parametrize("retention_ms", [True, 1.5, 2**63])
async def test_invalid_retention_is_rejected_before_redis_writes(
    journal: DeliveryJournal,
    retention_ms: Any,
) -> None:
    with pytest.raises(ValueError, match=re.escape(str(retention_ms))):
        DeliveryJournal(journal.client, journal.prefix, retention_ms=retention_ms)
    assert await journal.snapshot("one") == {}


async def test_missing_acl_permission_does_not_commit_partial_enqueue(
    journal: DeliveryJournal,
    redis_url: str,
) -> None:
    user = f"spike-{uuid.uuid4().hex}"
    await journal.client.execute_command(  # pyright: ignore[reportUnknownMemberType]
        "ACL",
        "SETUSER",
        user,
        "on",
        ">test-only",
        "~*",
        "+@all",
        "-zadd",
    )
    try:
        async with Redis.from_url(  # pyright: ignore[reportUnknownMemberType]
            redis_url,
            username=user,
            password="test-only",  # noqa: S106 -- Synthetic local test user.
            decode_responses=True,
        ) as client:
            restricted = DeliveryJournal(client, journal.prefix)
            with pytest.raises(ResponseError):
                await restricted.enqueue("one", encode(SendMessage(chat_id=42, text="acl")))
        assert await journal.snapshot("one") == {}
        assert await journal.outbox() == []
    finally:
        await journal.client.execute_command("ACL", "DELUSER", user)  # pyright: ignore[reportUnknownMemberType]
