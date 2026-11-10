"""Spike tasks using public Taskiq hooks; no production queue API is introduced."""

import asyncio
import hashlib
import inspect
import json
import logging
import os
import signal
from contextlib import suppress
from pathlib import Path
from typing import Annotated, Any

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from durable import run_delivery
from journal import DeliveryJournal
from payload import decode
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from taskiq import Context, SimpleRetryMiddleware, TaskiqDepends, TaskiqEvents, TaskiqState
from taskiq_redis import RedisStreamBroker

if (expected_source := os.environ.get("SPIKE_BROKER_SOURCE")) and (
    Path(inspect.getfile(RedisStreamBroker)).resolve() != Path(expected_source).resolve()
):
    raise RuntimeError(expected_source)
if (expected_digest := os.environ.get("SPIKE_BROKER_SHA256")) and (
    hashlib.sha256(Path(inspect.getfile(RedisStreamBroker)).read_bytes()).hexdigest()
    != expected_digest
):
    raise RuntimeError(expected_digest)

URL = os.environ["SPIKE_REDIS_URL"]
PREFIX = os.environ["SPIKE_PREFIX"]
MAX_ATTEMPTS = 3
logger = logging.getLogger(__name__)
broker = RedisStreamBroker(
    URL,
    queue_name=f"{PREFIX}:queue",
    consumer_group_name=f"{PREFIX}:group",
    consumer_id="0",
    xread_block=100,
    xread_count=1,
    idle_timeout=int(os.environ.get("SPIKE_IDLE_MS", "5000")),
    unacknowledged_batch_size=int(os.environ.get("SPIKE_RECLAIM_BATCH", "100")),
    unacknowledged_lock_timeout=1,
    socket_timeout=1,
    socket_connect_timeout=1,
    retry=Retry(NoBackoff(), 0),
).with_middlewares(SimpleRetryMiddleware(default_retry_count=3))


@broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def startup(state: TaskiqState) -> None:
    # The producer imports tasks but never creates a Bot or this application client.
    state.redis = application_client()
    session = AiohttpSession(
        api=TelegramAPIServer.from_base(os.environ["SPIKE_API_URL"]),
        timeout=2,
    )
    state.bot = Bot(token="123456:local-spike-token", session=session)  # noqa: S106
    state.http = await session.create_session()
    await state.redis.rpush(f"{PREFIX}:opened", str(os.getpid()))
    if os.environ.get("SPIKE_DURABLE") == "1":
        state.journal = DeliveryJournal(state.redis, PREFIX)
        state.dispatcher = asyncio.create_task(dispatch_loop(state.journal))


@broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)
async def shutdown(state: TaskiqState) -> None:
    if os.environ.get("SPIKE_DURABLE") == "1":
        state.dispatcher.cancel()
        with suppress(asyncio.CancelledError):
            await state.dispatcher
    await state.bot.session.close()
    if not state.http.closed:
        raise RuntimeError
    # Closing the broker-owned pool must not close this independent client.
    await state.redis.rpush(f"{PREFIX}:closed", str(os.getpid()))
    await state.redis.aclose()


async def gate(client: Redis, job: str, phase: str) -> None:
    await client.rpush(f"{PREFIX}:stages", f"{job}:{phase}")
    async with asyncio.timeout(30):
        while await client.blpop(f"{PREFIX}:release:{job}:{phase}", timeout=0.2) is None:
            pass


def application_client() -> Redis:
    return Redis.from_url(  # pyright: ignore[reportUnknownMemberType]
        URL,
        decode_responses=True,
        socket_timeout=1,
        socket_connect_timeout=1,
        retry=Retry(NoBackoff(), 0),
    )


async def dispatch_pending(journal: DeliveryJournal) -> None:
    for job, generation in await journal.outbox():
        state = await journal.snapshot(job)
        if state.get("generation") == generation and state.get("status") not in {
            "dead",
            "succeeded",
        }:
            await durable_deliver.kicker().with_task_id(f"{job}-{generation}").kiq(job, generation)
            if os.environ.get("SPIKE_DURABLE_PAUSE") == "after_publish":
                await gate(journal.client, job, "after_publish")
        await journal.dispatched(job, generation)


async def dispatch_loop(journal: DeliveryJournal) -> None:
    while True:
        try:
            await dispatch_pending(journal)
            await journal.cleanup()
        except Exception as exc:  # noqa: BLE001 -- Keep durable outbox for retry, log no payload.
            logger.error("Outbox dispatch failed: %s", type(exc).__name__)  # noqa: TRY400 -- No exception payload/credentials.
        await asyncio.sleep(0.1)


@broker.task(ack_type="manual")
async def durable_deliver(
    job: str,
    generation: str,
    context: Context = TaskiqDepends(),  # noqa: B008 -- Taskiq resolves this dependency marker.
) -> None:
    journal: DeliveryJournal = context.state.journal
    bot: Bot = context.state.bot

    async def pause(phase: str) -> None:
        if os.environ.get("SPIKE_DURABLE_PAUSE") == phase:
            await gate(journal.client, job, phase)

    status = await run_delivery(journal, bot, job, generation, context.ack, pause)
    if status == "succeeded":
        await journal.client.rpush(f"{PREFIX}:completed", job)


@broker.task(ack_type="manual")
async def deliver(
    wire: dict[str, Any],
    job: str,
    context: Annotated[Context, TaskiqDepends()],
    pause: str = "",
) -> None:
    client: Redis = context.state.redis
    bot: Bot = context.state.bot
    method = decode(wire)
    await client.rpush(f"{PREFIX}:owners:{job}", str(os.getpid()))
    if pause == "before":
        await gate(client, job, "before")
    # One experimental owner of attempts/delay; SimpleRetry is disabled on this task.
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            await bot(method)
        except (TelegramRetryAfter, TelegramNetworkError, TelegramServerError) as exc:
            if attempt < MAX_ATTEMPTS:
                delay = exc.retry_after if isinstance(exc, TelegramRetryAfter) else 0.05
                await asyncio.sleep(delay)
                continue
            failure = type(exc).__name__
        except TelegramBadRequest as exc:
            failure = type(exc).__name__
        else:
            if pause == "after":
                await gate(client, job, "after")
            await context.ack()
            await client.rpush(f"{PREFIX}:completed", job)
            return
        # Unexpected errors (including failed DLQ writes) leave the message pending.
        await client.xadd(
            f"{PREFIX}:dlq",
            {
                "job": job,
                "wire": json.dumps(wire),
                "attempts": attempt,
                "error": failure,
            },
        )
        await context.ack()
        return


@broker.task(retry_on_error=True, max_retries=3)
async def stock_retry(
    wire: dict[str, Any],
    context: Annotated[Context, TaskiqDepends()],
) -> None:
    bot: Bot = context.state.bot
    await bot(decode(wire))


@broker.task
async def finish() -> None:
    # Exercise Taskiq's installed signal handler without platform-specific console APIs.
    signal.raise_signal(signal.SIGINT)


async def produce() -> None:
    await broker.startup()
    try:
        kwargs = json.loads(os.environ.get("SPIKE_KWARGS", "{}"))
        if os.environ.get("SPIKE_TASK") in {"durable_enqueue", "durable_replay"}:
            async with application_client() as client:
                journal = DeliveryJournal(client, PREFIX)
                if os.environ["SPIKE_TASK"] == "durable_enqueue":
                    await journal.enqueue(kwargs["job"], kwargs["wire"])
                    if os.environ.get("SPIKE_DURABLE_PAUSE") == "after_enqueue":
                        await gate(client, kwargs["job"], "after_enqueue")
                else:
                    await journal.replay(kwargs["job"], kwargs["entry"])
                    if os.environ.get("SPIKE_DURABLE_PAUSE") == "after_replay":
                        await gate(client, kwargs["job"], "after_replay")
                await dispatch_pending(journal)
            return
        selected = {"deliver": deliver, "stock_retry": stock_retry, "finish": finish}[
            os.environ.get("SPIKE_TASK", "deliver")
        ]
        await selected.kiq(**kwargs)
    finally:
        await broker.shutdown()


if __name__ == "__main__":
    asyncio.run(produce())
