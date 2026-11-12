"""Exercise delivery through the public queue and aiogram's session boundary."""

import asyncio
import subprocess
import sys
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.client.default import Default
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import SendMessage
from aiogram.types import BufferedInputFile, FSInputFile, InputMediaPhoto, URLInputFile

from aiogram_extensions.integrations import Event, EventDispatcher, LimitExceededEvent, TaskEvent
from aiogram_extensions.messaging import (
    DeadLetterNotFoundError,
    DrainTimeoutError,
    EnqueueResult,
    IdempotencyConflictError,
    MessageQueue,
    QueueConfig,
    QueueError,
    QueueFullError,
    QueueNotRunningError,
    RetryConfig,
    SerializationError,
)
from aiogram_extensions.rate_limit import (
    MemoryRateLimitBackend,
    RateLimitConfig,
    RateLimiter,
    RateRule,
)
from aiogram_extensions.rate_limit.middleware import OutgoingRateLimitMiddleware


async def test_queue_drains_in_fifo_order_and_leaves_borrowed_session_open() -> None:
    async with Bot("123456:synthetic") as bot:
        transport = AsyncMock(return_value=True)
        bot.session.make_request = transport
        close = AsyncMock(wraps=bot.session.close)
        bot.session.close = close
        queue = MessageQueue(bot=bot)

        with pytest.raises(QueueNotRunningError):
            await queue.send_message(chat_id=1, text="before")
        await queue.start()
        for text in ("first", "second", "third"):
            await queue.send_message(chat_id=1, text=text)
        await queue.stop()

        assert [call.args[1].text for call in transport.await_args_list] == [
            "first",
            "second",
            "third",
        ]
        close.assert_not_awaited()
        with pytest.raises(QueueNotRunningError):
            await queue.send_message(chat_id=1, text="after")


async def test_queue_delivers_media_group_with_local_upload() -> None:
    async with Bot("123456:synthetic") as bot:
        transport = AsyncMock(return_value=[])
        bot.session.make_request = transport
        queue = MessageQueue(bot=bot)
        upload = BufferedInputFile(b"original", filename="photo.jpg")
        await queue.start()

        await queue.send_media_group(chat_id=1, media=[InputMediaPhoto(media=upload)])
        upload.data = b"changed by caller"
        await queue.stop()

        assert transport.await_args is not None
        method = transport.await_args.args[1]
        assert method.__api_method__ == "sendMediaGroup"
        assert method.media[0].media.data == b"original"


async def test_queue_retries_a_transient_failure_through_aiogram() -> None:
    async with Bot("123456:synthetic") as bot:
        request = SendMessage(chat_id=1, text="hello")
        transport = AsyncMock(
            side_effect=[TelegramNetworkError(method=request, message="offline"), True]
        )
        bot.session.make_request = transport
        queue = MessageQueue(bot=bot, config=QueueConfig(retry=RetryConfig(base_delay=0.001)))
        await queue.start()

        await queue.send(request)
        await queue.stop()

        assert transport.await_count == 2


async def test_queue_replays_a_dead_letter_once_and_updates_the_idempotency_key() -> None:
    failed = asyncio.Event()
    replays: list[TaskEvent] = []

    async def observe(event: Event) -> None:
        if isinstance(event, TaskEvent):
            if event.kind == "task_dlq":
                failed.set()
            elif event.kind == "dlq_replayed":
                replays.append(event)

    async with Bot("123456:synthetic") as bot:
        request = SendMessage(chat_id=1, text="hello")
        transport = AsyncMock(
            side_effect=[TelegramBadRequest(method=request, message="invalid"), True]
        )
        bot.session.make_request = transport
        queue = MessageQueue(bot=bot, events=EventDispatcher((observe,)))
        await queue.start()
        original = await queue.send(request, idempotency_key="hello")

        async with asyncio.timeout(2):
            await failed.wait()

        (entry,) = queue.dead_letters()
        assert (entry.task_id, entry.generation, entry.attempts, entry.reason) == (
            original.task_id,
            original.generation,
            1,
            "permanent",
        )
        first, second = await asyncio.gather(
            queue.replay(entry.task_id, entry.generation),
            queue.replay(entry.task_id, entry.generation),
        )
        duplicate = await queue.send(request, idempotency_key="hello")
        await queue.stop()

        assert first.task_id == original.task_id
        assert first.generation != original.generation
        assert not first.duplicate
        assert second == duplicate
        assert second.duplicate
        assert second.generation == first.generation
        assert queue.dead_letters() == ()
        assert transport.await_count == 2
        assert replays == [
            TaskEvent(kind="dlq_replayed", task_id=first.task_id, generation=first.generation)
        ]


async def test_queue_dispatches_each_required_send_method() -> None:
    async with Bot("123456:synthetic") as bot:
        transport = AsyncMock(return_value=True)
        bot.session.make_request = transport
        queue = MessageQueue(bot=bot)
        await queue.start()

        await queue.send_message(1, "message")
        await queue.send_photo(1, "file-id")
        await queue.send_document(1, FSInputFile("local.txt"))
        await queue.send_video(1, "https://example.test/movie.mp4")
        await queue.send_audio(1, URLInputFile("https://example.test/audio.mp3"))
        await queue.send_media_group(
            1, [InputMediaPhoto(media="one"), InputMediaPhoto(media="two")]
        )
        await queue.send_location(1, 12.5, 32.5)
        await queue.send_contact(1, "+1234567890", "Pat")
        await queue.stop()

        calls = [call.args[1] for call in transport.await_args_list]
        assert [method.__api_method__ for method in calls] == [
            "sendMessage",
            "sendPhoto",
            "sendDocument",
            "sendVideo",
            "sendAudio",
            "sendMediaGroup",
            "sendLocation",
            "sendContact",
        ]
        assert calls[2].document.path == "local.txt"
        assert calls[4].audio.url == "https://example.test/audio.mp3"
        assert calls[6].longitude == 32.5
        assert calls[7].first_name == "Pat"


async def test_invalid_or_oversized_payload_does_not_consume_capacity() -> None:
    async with Bot("123456:synthetic") as bot:
        bot.session.make_request = AsyncMock(return_value=True)
        queue = MessageQueue(bot=bot, config=QueueConfig(max_payload_bytes=1000, maxsize=1))
        await queue.start()

        with pytest.raises(SerializationError, match="exceeds"):
            await queue.send_photo(1, BufferedInputFile(b"x" * 1024, filename="large.png"))
        with pytest.raises(SerializationError, match="Invalid"):
            await queue.send_location(1, float("nan"), 0)
        await queue.send_message(1, "fits")
        await queue.stop()

        bot.session.make_request.assert_awaited_once()


async def test_capacity_includes_active_jobs_and_allows_idempotent_duplicates() -> None:
    release = asyncio.Event()
    both_started = asyncio.Event()
    starts: list[str] = []

    async def hold(_bot: Bot, method: SendMessage, **_kwargs: object) -> bool:
        starts.append(method.text)
        if len(starts) == 2:
            both_started.set()
        await release.wait()
        return True

    async with Bot("123456:synthetic") as bot:
        bot.session.make_request = AsyncMock(side_effect=hold)
        queue = MessageQueue(bot=bot, config=QueueConfig(workers=2, maxsize=2, overflow="reject"))
        await queue.start()
        try:
            first = await queue.send_message(1, "first", idempotency_key="key")
            await queue.send_message(1, "second")
            async with asyncio.timeout(2):
                await both_started.wait()

            duplicate = await queue.send_message(1, "first", idempotency_key="key")
            assert duplicate.duplicate
            assert duplicate.generation == first.generation
            with pytest.raises(IdempotencyConflictError):
                await queue.send_message(1, "different", idempotency_key="key")
            with pytest.raises(QueueFullError):
                await queue.send_message(1, "third")
        finally:
            release.set()
            await queue.stop()

        assert starts == ["first", "second"]


async def test_cancelled_waiter_is_never_admitted_and_stop_wakes_other_waiters() -> None:
    release = asyncio.Event()

    async def hold(*_args: object, **_kwargs: object) -> bool:
        await release.wait()
        return True

    async with Bot("123456:synthetic") as bot:
        transport = AsyncMock(side_effect=hold)
        bot.session.make_request = transport
        queue = MessageQueue(bot=bot, config=QueueConfig(maxsize=1))
        await queue.start()
        await queue.send_message(1, "accepted")
        cancelled = asyncio.create_task(queue.send_message(1, "cancelled"))
        blocked = asyncio.create_task(queue.send_message(1, "blocked"))
        await asyncio.sleep(0)

        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        stopper = asyncio.create_task(queue.stop())
        async with asyncio.timeout(2):
            with pytest.raises(QueueNotRunningError):
                await blocked
        stopper.cancel()
        with pytest.raises(asyncio.CancelledError):
            await stopper
        release.set()
        await queue.stop()

        assert [call.args[1].text for call in transport.await_args_list] == ["accepted"]


async def test_second_stop_waits_for_delivery_after_the_first_caller_is_cancelled() -> None:
    delivering = asyncio.Event()
    release = asyncio.Event()
    stopping = asyncio.Event()

    async def deliver(*_args: object, **_kwargs: object) -> bool:
        delivering.set()
        await release.wait()
        return True

    async def stop() -> None:
        stopping.set()
        await queue.stop()

    async with Bot("123456:synthetic") as bot:
        transport = AsyncMock(side_effect=deliver)
        bot.session.make_request = transport
        queue = MessageQueue(bot=bot)
        async with queue:
            try:
                await queue.send_message(1, "finish delivery")
                async with asyncio.timeout(2):
                    await delivering.wait()
                    first = asyncio.create_task(stop())
                    await stopping.wait()
                    first.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await first

                    with pytest.raises(QueueNotRunningError):
                        await queue.send_message(1, "admission closed")
            finally:
                release.set()

            await queue.stop()

    transport.assert_awaited_once()
    assert queue.dead_letters() == ()


def test_cancelled_stop_can_finish_when_asyncio_runner_closes() -> None:
    script = """
import asyncio

from aiogram import Bot
from aiogram_extensions.messaging import MessageQueue

async def main():
    started = asyncio.Event()
    stopping = asyncio.Event()

    async def send(*args, **kwargs):
        started.set()
        try:
            await asyncio.Future()
        finally:
            await asyncio.sleep(0)
            print("request cleanup finished", flush=True)

    def factory():
        bot = Bot("123456:synthetic")
        close = bot.session.close

        async def record_close():
            await close()
            print("owned session closed", flush=True)

        bot.session.make_request = send
        bot.session.close = record_close
        return bot

    queue = MessageQueue(bot_factory=factory)
    await queue.start()
    await queue.send_message(1, "pending")
    await started.wait()

    async def stop():
        stopping.set()
        await queue.stop()

    caller = asyncio.create_task(stop())
    await stopping.wait()
    caller.cancel()
    try:
        await caller
    except asyncio.CancelledError:
        pass
    print("main finished", flush=True)

asyncio.run(main())
print("runner closed")
"""

    result = subprocess.run(  # noqa: S603 -- Fixed test script with a mock transport.
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=8, check=False
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "main finished",
        "request cleanup finished",
        "owned session closed",
        "runner closed",
    ]


async def test_delivery_cancelled_without_cancelling_the_worker_goes_to_dlq() -> None:
    async with Bot("123456:synthetic") as bot:
        transport = AsyncMock(side_effect=[asyncio.CancelledError, True])
        bot.session.make_request = transport
        queue = MessageQueue(bot=bot)

        async with queue:
            first = await queue.send_message(1, "cancelled request")
            await queue.send_message(1, "next request")

    assert transport.await_count == 2
    (entry,) = queue.dead_letters()
    assert (entry.generation, entry.reason, entry.attempts) == (first.generation, "cancelled", 1)


@pytest.mark.parametrize("delegated", [False, True], ids=["direct", "child-task"])
async def test_worker_callback_cannot_wait_for_capacity_in_its_own_queue(
    *, delegated: bool
) -> None:
    delivered = asyncio.Event()
    rejections: list[QueueFullError] = []

    async def observe(event: Event) -> None:
        if isinstance(event, TaskEvent) and event.kind == "task_started":
            try:
                request = queue.send_message(1, "from callback")
                if delegated:
                    await asyncio.gather(request)
                else:
                    await request
            except QueueFullError as error:
                rejections.append(error)

    async def deliver(*_args: object, **_kwargs: object) -> bool:
        delivered.set()
        return True

    async with Bot("123456:synthetic") as bot:
        transport = AsyncMock(side_effect=deliver)
        bot.session.make_request = transport
        queue = MessageQueue(
            bot=bot, config=QueueConfig(maxsize=1), events=EventDispatcher((observe,))
        )

        async with queue:
            await queue.send_message(1, "original")
            async with asyncio.timeout(1):
                await delivered.wait()

    assert len(rejections) == 1
    transport.assert_awaited_once()


async def test_worker_callback_cannot_wait_for_its_own_queue_to_stop_in_a_child_task() -> None:
    rejections: list[QueueError] = []

    async def observe(event: Event) -> None:
        if isinstance(event, TaskEvent) and event.kind == "task_started":
            try:
                await asyncio.gather(queue.stop())
            except QueueError as error:
                rejections.append(error)

    async with Bot("123456:synthetic") as bot:
        transport = AsyncMock(return_value=True)
        bot.session.make_request = transport
        queue = MessageQueue(
            bot=bot, config=QueueConfig(drain_timeout=0.2), events=EventDispatcher((observe,))
        )
        await queue.start()
        await queue.send_message(1, "original")

        async with asyncio.timeout(1):
            await queue.stop()

    assert len(rejections) == 1
    transport.assert_awaited_once()


async def test_detached_callback_task_can_wait_for_capacity_after_callback_returns() -> None:
    delivering = asyncio.Event()
    attempting = asyncio.Event()
    release = asyncio.Event()
    tasks: list[asyncio.Task[EnqueueResult]] = []

    async def submit() -> EnqueueResult:
        await delivering.wait()
        attempting.set()
        return await queue.send_message(1, "follow-up")

    async def observe(event: Event) -> None:
        if isinstance(event, TaskEvent) and event.kind == "task_started" and not tasks:
            tasks.append(asyncio.create_task(submit()))

    async def deliver(*_args: object, **_kwargs: object) -> bool:
        delivering.set()
        await release.wait()
        return True

    async with Bot("123456:synthetic") as bot:
        transport = AsyncMock(side_effect=deliver)
        bot.session.make_request = transport
        queue = MessageQueue(
            bot=bot, config=QueueConfig(maxsize=1), events=EventDispatcher((observe,))
        )

        async with queue:
            await queue.send_message(1, "original")
            async with asyncio.timeout(2):
                try:
                    await attempting.wait()
                finally:
                    release.set()
                await tasks[0]

    assert [call.args[1].text for call in transport.await_args_list] == ["original", "follow-up"]


async def test_limiter_callback_child_cannot_block_delivery_on_its_own_queue() -> None:
    rejected = asyncio.Event()

    async def observe(event: Event) -> None:
        if isinstance(event, LimitExceededEvent):
            try:
                await asyncio.gather(queue.send_message(1, "from limiter callback"))
            except QueueFullError:
                rejected.set()

    limiter = RateLimiter(
        RateLimitConfig(rules=(RateRule(name="method", scope="method", limit=1, window=60),)),
        direction="outgoing",
        backend=MemoryRateLimitBackend(clock=lambda: 0.0),
        events=EventDispatcher((observe,)),
    )
    async with Bot("123456:synthetic") as bot:
        transport = AsyncMock(return_value=True)
        bot.session.make_request = transport
        bot.session.middleware(OutgoingRateLimitMiddleware(limiter))
        queue = MessageQueue(
            bot=bot, config=QueueConfig(maxsize=1, retry=RetryConfig(max_attempts=1))
        )
        await bot.send_message(1, "consume quota")

        async with queue:
            await queue.send_message(1, "rate limited")
            async with asyncio.timeout(2):
                await rejected.wait()

    transport.assert_awaited_once()
    (entry,) = queue.dead_letters()
    assert entry.reason == "rate_limited"


async def test_drain_timeout_cancels_active_delivery_and_retains_pending_jobs() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def hold(*_args: object, **_kwargs: object) -> bool:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        return True

    async with Bot("123456:synthetic") as bot:
        close = AsyncMock(wraps=bot.session.close)
        bot.session.close = close
        bot.session.make_request = AsyncMock(side_effect=hold)
        queue = MessageQueue(
            bot_factory=lambda: bot,
            config=QueueConfig(maxsize=2, drain_timeout=0.01, enqueue_timeout=0.001),
        )
        await queue.start()
        active = await queue.send_message(1, "active")
        queued = await queue.send_message(1, "queued")
        async with asyncio.timeout(2):
            await started.wait()
        with pytest.raises(QueueFullError, match="deadline"):
            await queue.send_message(1, "timed out waiting")

        with pytest.raises(DrainTimeoutError):
            await queue.stop()

        assert cancelled.is_set()
        close.assert_awaited_once()
        assert [
            (entry.task_id, entry.reason, entry.attempts) for entry in queue.dead_letters()
        ] == [
            (active.task_id, "drain_timeout", 1),
            (queued.task_id, "drain_timeout", 0),
        ]


async def test_queue_applies_flood_wait_with_outgoing_middleware_and_bounded_retry() -> None:
    observed: list[TaskEvent] = []

    async def observe(event: Event) -> None:
        if isinstance(event, TaskEvent):
            observed.append(event)

    async with Bot("123456:synthetic") as bot:
        limiter = RateLimiter(
            RateLimitConfig(rules=(RateRule(name="send", scope="bot", limit=10, window=1),)),
            direction="outgoing",
        )
        bot.session.middleware(OutgoingRateLimitMiddleware(limiter))
        method = SendMessage(chat_id=1, text="hello")
        transport = AsyncMock(
            side_effect=TelegramRetryAfter(method=method, message="slow down", retry_after=1)
        )
        bot.session.make_request = transport
        queue = MessageQueue(
            bot=bot,
            events=EventDispatcher((observe,)),
            config=QueueConfig(
                retry=RetryConfig(max_attempts=2, base_delay=0.001, max_delay=0.001)
            ),
            retry_policy=lambda _error, _attempt: 0,
        )
        await queue.start()

        await queue.send(method)
        await queue.stop()

        assert transport.await_count == 2
        assert [(event.kind, event.attempt, event.retry_after) for event in observed] == [
            ("task_enqueued", 0, 0),
            ("task_started", 1, 0),
            ("retry_scheduled", 1, 1),
            ("task_started", 2, 0),
            ("task_failed", 2, 0),
            ("task_dlq", 2, 0),
        ]
        assert queue.dead_letters()[0].reason == "rate_limited"


async def test_retry_backoff_grows_and_stays_at_the_cap() -> None:
    delays: list[float] = []

    async def observe(event: Event) -> None:
        if isinstance(event, TaskEvent) and event.kind == "retry_scheduled":
            delays.append(event.retry_after)

    async with Bot("123456:synthetic") as bot:
        method = SendMessage(chat_id=1, text="hello")
        transport = AsyncMock(side_effect=TelegramNetworkError(method=method, message="offline"))
        bot.session.make_request = transport
        queue = MessageQueue(
            bot=bot,
            config=QueueConfig(
                retry=RetryConfig(max_attempts=5, base_delay=0.001, max_delay=0.003)
            ),
            events=EventDispatcher((observe,)),
        )

        async with queue:
            await queue.send(method)

    assert delays == [0.001, 0.002, 0.003, 0.003]
    assert transport.await_count == 5


@pytest.mark.parametrize(
    ("delay", "reason"),
    [
        pytest.param(None, "transient", id="stop"),
        pytest.param(-1, "retry_policy_error", id="invalid"),
    ],
)
async def test_custom_policy_can_stop_a_retry_and_invalid_delays_fail_delivery(
    delay: float | None, reason: str
) -> None:
    async with Bot("123456:synthetic") as bot:
        method = SendMessage(chat_id=1, text="hello")
        transport = AsyncMock(side_effect=TelegramNetworkError(method=method, message="offline"))
        bot.session.make_request = transport
        queue = MessageQueue(bot=bot, retry_policy=lambda _error, _attempt: delay)

        async with queue:
            await queue.send(method)

    transport.assert_awaited_once()
    (entry,) = queue.dead_letters()
    assert (entry.reason, entry.attempts) == (reason, 1)


async def test_active_keys_do_not_expire_and_terminal_ttls_are_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 0.0
    monkeypatch.setattr("aiogram_extensions.messaging.queue.monotonic", lambda: now)
    terminal = asyncio.Event()

    async def observe(event: Event) -> None:
        if isinstance(event, TaskEvent) and event.kind == "task_dlq":
            terminal.set()

    async with Bot("123456:synthetic") as bot:
        method = SendMessage(chat_id=1, text="first")
        bot.session.make_request = AsyncMock(
            side_effect=[TelegramBadRequest(method=method, message="failed"), True]
        )
        queue = MessageQueue(
            bot=bot,
            events=EventDispatcher((observe,)),
            config=QueueConfig(terminal_ttl=2, idempotency_ttl=5),
        )
        await queue.start()
        first = await queue.send(method, idempotency_key="key")
        now = 100
        assert (await queue.send(method, idempotency_key="key")).duplicate
        async with asyncio.timeout(2):
            await terminal.wait()

        now = 103
        assert queue.dead_letters() == ()
        assert (await queue.send(method, idempotency_key="key")).duplicate
        with pytest.raises(DeadLetterNotFoundError):
            await queue.replay(first.task_id, first.generation)

        now = 106
        assert not (await queue.send_message(1, "replacement", idempotency_key="key")).duplicate
        await queue.stop()


async def test_wait_mode_admits_one_job_for_concurrent_waiters_with_the_same_key() -> None:
    release = asyncio.Event()

    async def hold(*_args: object, **_kwargs: object) -> bool:
        await release.wait()
        return True

    async with Bot("123456:synthetic") as bot:
        transport = AsyncMock(side_effect=hold)
        bot.session.make_request = transport
        queue = MessageQueue(bot=bot, config=QueueConfig(maxsize=1, enqueue_timeout=2))
        await queue.start()
        await queue.send_message(1, "initial")
        waiting = [
            asyncio.create_task(queue.send_message(1, "next", idempotency_key="shared"))
            for _ in range(3)
        ]
        await asyncio.sleep(0)
        assert all(not task.done() for task in waiting)

        release.set()
        receipts = await asyncio.gather(*waiting)
        await queue.stop()

        assert sum(not receipt.duplicate for receipt in receipts) == 1
        assert len({receipt.generation for receipt in receipts}) == 1
        assert [call.args[1].text for call in transport.await_args_list] == ["initial", "next"]


@pytest.mark.parametrize(
    ("retry_unknown", "attempts"),
    [pytest.param(False, 1, id="default"), pytest.param(True, 2, id="opt-in")],
)
async def test_unknown_errors_follow_the_configured_policy(
    *, retry_unknown: bool, attempts: int
) -> None:
    async with Bot("123456:synthetic") as bot:
        transport = AsyncMock(side_effect=RuntimeError("unexpected"))
        bot.session.make_request = transport
        queue = MessageQueue(
            bot=bot,
            config=QueueConfig(
                retry=RetryConfig(max_attempts=2, base_delay=0.001, retry_unknown=retry_unknown)
            ),
        )
        await queue.start()
        await queue.send_message(1, "hello")

        await queue.stop()

        (entry,) = queue.dead_letters()
        assert entry.attempts == transport.await_count == attempts
        assert entry.reason == "unknown"


async def test_queue_context_drains_and_closes_factory_sessions_on_each_run() -> None:
    sessions: list[AsyncMock] = []
    transport = AsyncMock(return_value=True)

    def make_bot() -> Bot:
        bot = Bot("123456:synthetic")
        bot.session.make_request = transport
        close = AsyncMock(wraps=bot.session.close)
        bot.session.close = close
        sessions.append(close)
        return bot

    queue = MessageQueue(bot_factory=make_bot)
    async with queue as running:
        assert running is queue
        first = await running.send_message(1, "hello", idempotency_key="greeting")
    async with queue:
        duplicate = await queue.send_message(1, "hello", idempotency_key="greeting")

    assert duplicate.duplicate
    assert duplicate.generation == first.generation
    assert len(sessions) == 2
    for close in sessions:
        close.assert_awaited_once()
    transport.assert_awaited_once()


async def test_invalid_convenience_arguments_use_the_queue_error_contract() -> None:
    async with Bot("123456:synthetic") as bot:
        queue = MessageQueue(bot=bot)
        await queue.start()
        try:
            with pytest.raises(SerializationError, match="sendLocation") as raised:
                await queue.send_location(1, 12.5, 32.5, horizontal_accuracy="secret invalid value")
        finally:
            await queue.stop()

        assert "secret invalid value" not in str(raised.value)


async def test_invalid_album_uses_the_queue_error_contract() -> None:
    async with Bot("123456:synthetic") as bot, MessageQueue(bot=bot) as queue:
        with pytest.raises(SerializationError, match="sendMediaGroup"):
            await queue.send_media_group(1, media=None)  # pyright: ignore[reportArgumentType]


async def test_idempotency_distinguishes_a_default_from_a_similarly_shaped_mapping() -> None:
    async with Bot("123456:synthetic") as bot:
        bot.session.make_request = AsyncMock(return_value=True)
        async with MessageQueue(bot=bot) as queue:
            await queue.send_message(
                1, "hello", future_option=Default("parse_mode"), idempotency_key="one"
            )

            with pytest.raises(IdempotencyConflictError):
                await queue.send_message(
                    1, "hello", future_option={"$default": "parse_mode"}, idempotency_key="one"
                )


async def test_timeout_closes_the_session_and_bounds_observers_before_replay() -> None:
    started = asyncio.Event()
    observer_cancelled = asyncio.Event()
    sessions: list[AsyncMock] = []
    delivered = AsyncMock(return_value=True)

    async def hang(*_args: object, **_kwargs: object) -> None:
        started.set()
        await asyncio.Event().wait()

    async def observe(event: Event) -> None:
        if isinstance(event, TaskEvent) and event.kind == "task_dlq":
            sessions[0].assert_awaited_once()
            try:
                await asyncio.Event().wait()
            finally:
                observer_cancelled.set()

    def make_bot() -> Bot:
        bot = Bot("123456:synthetic")
        bot.session.make_request = delivered if sessions else AsyncMock(side_effect=hang)
        close = AsyncMock(wraps=bot.session.close)
        bot.session.close = close
        sessions.append(close)
        return bot

    queue = MessageQueue(
        bot_factory=make_bot,
        config=QueueConfig(drain_timeout=0.03),
        events=EventDispatcher((observe,)),
    )
    await queue.start()
    original = await queue.send_message(1, "recover me")
    async with asyncio.timeout(2):
        await started.wait()
        with pytest.raises(DrainTimeoutError):
            await queue.stop()

    assert observer_cancelled.is_set()
    async with queue:
        replayed = await queue.replay(original.task_id, original.generation)

    assert replayed.generation != original.generation
    assert queue.dead_letters() == ()
    delivered.assert_awaited_once()


async def test_session_close_failure_keeps_drain_timeout_and_terminal_events() -> None:
    started = asyncio.Event()
    events: list[TaskEvent] = []

    async def request(*_args: object, **_kwargs: object) -> None:
        started.set()
        await asyncio.Future[None]()

    async def observe(event: Event) -> None:
        if isinstance(event, TaskEvent):
            events.append(event)

    close = AsyncMock(side_effect=OSError("close failed"))

    def factory() -> Bot:
        bot = Bot("123456:synthetic")
        bot.session.make_request = AsyncMock(side_effect=request)
        bot.session.close = close
        return bot

    queue = MessageQueue(
        bot_factory=factory,
        config=QueueConfig(drain_timeout=0.05),
        events=EventDispatcher((observe,)),
    )
    await queue.start()
    receipt = await queue.send_message(1, "interrupted")
    async with asyncio.timeout(2):
        await started.wait()
        with pytest.raises(DrainTimeoutError) as raised:
            await queue.stop()

    close.assert_awaited_once()
    assert raised.value.__notes__ == ["Closing the owned Bot session also failed"]
    (entry,) = queue.dead_letters()
    assert (entry.generation, entry.reason) == (receipt.generation, "drain_timeout")
    assert [event.kind for event in events] == [
        "task_enqueued",
        "task_started",
        "task_failed",
        "task_dlq",
    ]


async def test_unexpected_worker_exit_fails_stop_and_preserves_the_interrupted_job() -> None:
    class ObserverError(BaseException):
        pass

    crashed = False

    async def observe(event: Event) -> None:
        nonlocal crashed
        if isinstance(event, TaskEvent) and event.kind == "task_started" and not crashed:
            crashed = True
            raise ObserverError

    async with Bot("123456:synthetic") as bot:
        transport = AsyncMock(return_value=True)
        bot.session.make_request = transport
        queue = MessageQueue(
            bot=bot, config=QueueConfig(maxsize=2), events=EventDispatcher((observe,))
        )
        await queue.start()
        receipt = await queue.send_message(1, "recover me")
        waiting = await queue.send_message(1, "still queued")

        with pytest.raises(QueueError, match="worker failed"):
            await queue.stop()

        transport.assert_not_awaited()
        entry, pending = queue.dead_letters()
        assert (entry.task_id, entry.generation, entry.reason, entry.attempts) == (
            receipt.task_id,
            receipt.generation,
            "worker_failed",
            1,
        )
        assert (pending.generation, pending.reason, pending.attempts) == (
            waiting.generation,
            "worker_failed",
            0,
        )

        async with queue:
            replayed = await queue.replay(entry.task_id, entry.generation)

        assert replayed.generation != receipt.generation
        transport.assert_awaited_once()
