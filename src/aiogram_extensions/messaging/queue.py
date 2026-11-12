"""Local queue lifecycle, admission and dead-letter replay."""

import asyncio
import logging
from collections.abc import Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field, replace
from time import monotonic
from types import TracebackType
from typing import Literal, Self, cast
from uuid import uuid4

from aiogram import Bot

from aiogram_extensions._validation import ConfigurationError, nonempty_string
from aiogram_extensions.integrations import EventDispatcher, TaskEvent, TaskEventKind

from ._methods import SendMethods
from ._payload import SendMethod, snapshot
from ._retention import Retained
from .config import QueueConfig
from .errors import (
    DeadLetterNotFoundError,
    DrainTimeoutError,
    IdempotencyConflictError,
    QueueError,
    QueueFullError,
    QueueNotRunningError,
)
from .models import EnqueueResult
from .retry import FailureReason, RetryPolicy, deliver

_DEFAULT_CONFIG = QueueConfig()
logger = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class DeadLetter:
    """A failed delivery and a detached copy of its Telegram request."""

    task_id: str
    generation: str
    attempts: int
    reason: FailureReason
    method: SendMethod = field(repr=False)


@dataclass(frozen=True)
class _Identity:
    result: EnqueueResult
    fingerprint: bytes


@dataclass
class _Job:
    identity: _Identity
    method: SendMethod = field(repr=False)
    key: str | None = field(repr=False)
    attempt: int = 0

    @property
    def result(self) -> EnqueueResult:
        return self.identity.result


@dataclass
class _CallbackScope:
    active: bool = True


class MessageQueue(SendMethods):
    """Bounded, single-process queue bound to one event loop."""

    def __init__(
        self,
        *,
        bot: Bot | None = None,
        bot_factory: Callable[[], Bot] | None = None,
        config: QueueConfig = _DEFAULT_CONFIG,
        backend: Literal["memory"] = "memory",
        retry_policy: RetryPolicy | None = None,
        events: EventDispatcher | None = None,
    ) -> None:
        if backend != "memory":
            raise ConfigurationError("backend", "must be memory")
        if (bot is None) == (bot_factory is None):
            raise ConfigurationError("bot", "provide exactly one of bot and bot_factory")

        self._bot = bot
        self._bot_factory = bot_factory
        self._config = config
        self._retry_policy = retry_policy
        self._events = events
        self._ready = asyncio.Queue[_Job]()
        self._changed = asyncio.Event()
        self._pending: dict[str, _Job] = {}
        self._active_keys: dict[str, _Identity] = {}
        self._terminal_keys = Retained[str, _Identity](config.idempotency_ttl)
        self._dead = Retained[tuple[str, str], tuple[_Job, FailureReason] | EnqueueResult](
            config.terminal_ttl
        )
        self._workers: list[asyncio.Task[None]] = []
        self._shutdown: asyncio.Task[None] | None = None
        self._aborting = False
        self._context_active = False
        self._callback_scope: ContextVar[_CallbackScope | None] = ContextVar(
            "message_queue_callback_scope", default=None
        )

    async def __aenter__(self) -> Self:
        if self._context_active:
            raise QueueError("The queue is already managed by a context")
        await self.start()
        self._context_active = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            await self.stop()
        finally:
            self._context_active = False

    async def start(self) -> None:
        """Start workers or restart after the previous stop has finished."""
        if self._shutdown is not None and not self._shutdown.done():
            raise QueueNotRunningError("Wait for the queue to stop before restarting")
        if self._workers:
            return

        bot = self._bot_factory() if self._bot_factory is not None else self._bot
        if not isinstance(bot, Bot):
            raise ConfigurationError("bot", "must be an aiogram Bot")

        self._bot = bot
        self._ready = asyncio.Queue[_Job]()
        self._aborting = False
        self._shutdown = None
        self._workers = [
            asyncio.create_task(self._worker(bot)) for _ in range(self._config.workers)
        ]
        for worker in self._workers:
            worker.add_done_callback(lambda _: self._wake_waiters())

    async def stop(self) -> None:
        """Stop accepting tasks and wait for the current run to drain."""
        caller = asyncio.current_task()
        if caller in self._workers or caller is self._shutdown or self._in_worker_callback():
            raise QueueError("A worker or shutdown callback cannot await its own queue stopping")
        if self._shutdown is None:
            if not self._workers:
                return
            self._shutdown = asyncio.create_task(self._drain())
            self._shutdown.add_done_callback(_observe_shutdown)
            self._wake_waiters()

        await asyncio.shield(self._shutdown)

    async def send(
        self, method: SendMethod, *, idempotency_key: str | None = None
    ) -> EnqueueResult:
        self._require_running()
        if idempotency_key is not None:
            nonempty_string("idempotency_key", idempotency_key)

        request, fingerprint = snapshot(method, self._config.max_payload_bytes)
        identity = _Identity(
            EnqueueResult(task_id=uuid4().hex, generation=uuid4().hex), fingerprint
        )
        return await self._admit(_Job(identity, request, idempotency_key))

    def cleanup(self) -> None:
        """Discard expired idempotency keys, DLQ entries and replay receipts."""
        now = monotonic()
        self._terminal_keys.expire(now)
        self._dead.expire(now)

    def dead_letters(self) -> tuple[DeadLetter, ...]:
        """Return retained failed generations in failure order."""
        self.cleanup()
        return tuple(
            DeadLetter(
                task_id=job.result.task_id,
                generation=job.result.generation,
                attempts=job.attempt,
                reason=reason,
                method=deepcopy(job.method),
            )
            for record in self._dead.values()
            if not isinstance(record, EnqueueResult)
            for job, reason in (record,)
        )

    async def replay(self, task_id: str, generation: str) -> EnqueueResult:
        return await self._admit((task_id, generation))

    def _require_running(self) -> None:
        if (
            not self._workers
            or self._shutdown is not None
            or any(worker.done() for worker in self._workers)
        ):
            raise QueueNotRunningError("The queue is not accepting work")

    def _identity(self, key: str) -> _Identity | None:
        return self._active_keys.get(key) or self._terminal_keys.get(key)

    def _resolve(self, request: _Job | tuple[str, str]) -> _Job | EnqueueResult:
        if isinstance(request, tuple):
            record = self._dead.get(request)
            if record is None:
                raise DeadLetterNotFoundError("No retained dead letter for that generation")
            return record if isinstance(record, EnqueueResult) else record[0]

        existing = self._identity(request.key) if request.key is not None else None
        if existing is None:
            return request
        if existing.fingerprint != request.identity.fingerprint:
            raise IdempotencyConflictError("The key identifies a different message payload")
        return existing.result

    async def _admit(self, request: _Job | tuple[str, str]) -> EnqueueResult:
        ready = self._ready
        try:
            async with asyncio.timeout(self._config.enqueue_timeout):
                while True:
                    self._require_running()
                    if ready is not self._ready:
                        raise QueueNotRunningError("The queue stopped while waiting for capacity")
                    self.cleanup()
                    job = self._resolve(request)
                    if isinstance(job, EnqueueResult):
                        return replace(job, duplicate=True)
                    if len(self._pending) < self._config.maxsize:
                        break
                    # A worker callback can hold the slot it would wait for.
                    if (
                        self._config.overflow == "reject"
                        or asyncio.current_task() in self._workers
                        or self._in_worker_callback()
                    ):
                        raise QueueFullError("The queue is full")
                    await self._changed.wait()
        except TimeoutError as exc:
            raise QueueFullError("The enqueue deadline expired") from exc

        # No await between checking capacity, replacing a DLQ record and publishing the job.
        if isinstance(request, tuple):
            job = self._replace_dead_letter(request, job)
        if job.key is not None:
            self._active_keys[job.key] = job.identity
        self._pending[job.result.generation] = job
        self._ready.put_nowait(job)
        if isinstance(request, tuple):
            await self._emit("dlq_replayed", job, attempt=0)
        await self._emit("task_enqueued", job, attempt=0)
        return job.result

    def _replace_dead_letter(self, source: tuple[str, str], job: _Job) -> _Job:
        key = job.key
        if key is not None:
            if self._identity(key) is job.identity:
                self._terminal_keys.discard(key)
            else:
                key = None

        result = EnqueueResult(task_id=job.result.task_id, generation=uuid4().hex)
        self._dead.put(source, result, monotonic())
        return _Job(_Identity(result, job.identity.fingerprint), job.method, key)

    def _wake_waiters(self) -> None:
        self._changed.set()
        self._changed = asyncio.Event()

    def _in_worker_callback(self) -> bool:
        scope = self._callback_scope.get()
        return scope is not None and scope.active

    @contextmanager
    def _worker_callback_scope(self) -> Generator[None, None, None]:
        scope = _CallbackScope()
        token = self._callback_scope.set(scope)
        try:
            yield
        finally:
            # Child tasks inherit this object and can outlive the call.
            scope.active = False
            self._callback_scope.reset(token)

    async def _emit(
        self, kind: TaskEventKind, job: _Job, retry_after: float = 0, *, attempt: int | None = None
    ) -> None:
        if self._events is None:
            return

        event = TaskEvent(
            kind=kind,
            task_id=job.result.task_id,
            generation=job.result.generation,
            attempt=job.attempt if attempt is None else attempt,
            retry_after=retry_after,
        )
        caller = asyncio.current_task()
        if self._in_worker_callback() or (
            caller not in self._workers and caller is not self._shutdown
        ):
            await self._events.emit(event)
            return

        with self._worker_callback_scope():
            await self._events.emit(event)

    async def _deliver(self, bot: Bot, job: _Job) -> FailureReason | None:
        async def send() -> None:
            with self._worker_callback_scope():
                await deepcopy(job.method).emit(bot)

        async def observe(attempt: int, delay: float | None) -> None:
            job.attempt = attempt
            if delay is None:
                await self._emit("task_started", job)
            else:
                await self._emit("retry_scheduled", job, delay)

        return await deliver(send, self._config.retry, self._retry_policy, observe)

    def _finish(self, job: _Job, reason: FailureReason | None) -> None:
        self.cleanup()
        now = monotonic()
        del self._pending[job.result.generation]
        if job.key is not None:
            del self._active_keys[job.key]
            self._terminal_keys.put(job.key, job.identity, now)
        if reason is not None:
            self._dead.put((job.result.task_id, job.result.generation), (job, reason), now)
        self._wake_waiters()

    async def _emit_terminal(self, job: _Job, reason: FailureReason | None) -> None:
        if reason is None:
            await self._emit("task_completed", job)
        else:
            await self._emit("task_failed", job)
            await self._emit("task_dlq", job)

    async def _worker(self, bot: Bot) -> None:
        worker = cast("asyncio.Task[None]", asyncio.current_task())
        while not self._aborting and not worker.cancelling():
            job = await self._ready.get()
            try:
                try:
                    reason = await self._deliver(bot, job)
                except asyncio.CancelledError:
                    if self._aborting or worker.cancelling():
                        raise
                    reason = "cancelled"

                self._finish(job, reason)
                await self._emit_terminal(job, reason)
            except asyncio.CancelledError:
                if self._aborting or worker.cancelling():
                    raise
            finally:
                self._ready.task_done()

    async def _wait_for_workers(self) -> Literal["worker_failed", "drain_timeout"] | None:
        joined = asyncio.create_task(self._ready.join())
        try:
            done, _ = await asyncio.wait(
                (joined, *self._workers),
                timeout=self._config.drain_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            self._aborting = True
            joined.cancel()
            for worker in self._workers:
                if not worker.cancelling():
                    worker.cancel()
            try:
                _, *worker_results = await asyncio.gather(
                    joined, *self._workers, return_exceptions=True
                )
            finally:
                self._workers.clear()

        if done - {joined} or any(
            isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError)
            for result in worker_results
        ):
            return "worker_failed"
        return None if joined in done else "drain_timeout"

    def _fail_unfinished(self, reason: FailureReason) -> tuple[_Job, ...]:
        unfinished = tuple(self._pending.values())
        self._ready = asyncio.Queue[_Job]()
        for job in unfinished:
            self._finish(job, reason)
        return unfinished

    async def _close_owned_bot(self) -> None:
        if self._bot_factory is not None and self._bot is not None:
            try:
                await self._bot.session.close()
            finally:
                self._bot = None

    async def _drain(self) -> None:
        failure: BaseException | None = None
        reason: FailureReason | None
        try:
            reason = await self._wait_for_workers()
        except asyncio.CancelledError as error:
            reason = "cancelled"
            failure = error
        else:
            if reason == "drain_timeout":
                failure = DrainTimeoutError("The drain deadline expired")
            elif reason == "worker_failed":
                failure = QueueError("A queue worker failed during delivery")

        unfinished = self._fail_unfinished(reason) if reason is not None else ()
        try:
            await self._close_owned_bot()
        except Exception:
            if failure is None:
                raise
            failure.add_note("Closing the owned Bot session also failed")
            logger.warning("Closing the owned Bot session failed during interrupted drain")

        if reason in ("drain_timeout", "worker_failed"):
            await self._notify_interrupted(unfinished, reason)
        if failure is not None:
            raise failure

    async def _notify_interrupted(self, jobs: tuple[_Job, ...], reason: FailureReason) -> None:
        try:
            async with asyncio.timeout(self._config.drain_timeout):
                for job in jobs:
                    await self._emit_terminal(job, reason)
        except TimeoutError:
            logger.warning("Timed out notifying observers of interrupted deliveries")


def _observe_shutdown(task: asyncio.Task[None]) -> None:
    # A cancelled stop caller leaves the shielded drain running.
    if not task.cancelled():
        task.exception()
