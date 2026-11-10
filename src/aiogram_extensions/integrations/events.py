"""Typed observations, deliberately excluding secrets and message payloads."""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, TypeAlias

logger = logging.getLogger(__name__)

TaskEventKind = Literal[
    "task_enqueued",
    "task_started",
    "task_completed",
    "task_failed",
    "retry_scheduled",
    "task_dlq",
    "dlq_replayed",
]


@dataclass(frozen=True, kw_only=True)
class TaskEvent:
    kind: TaskEventKind
    task_id: str
    generation: str
    attempt: int = 0
    retry_after: float = 0.0


@dataclass(frozen=True, kw_only=True)
class LimitExceededEvent:
    direction: Literal["incoming", "outgoing"]
    triggered_rules: tuple[str, ...]
    retry_after: float


@dataclass(frozen=True, kw_only=True)
class BackendErrorEvent:
    component: Literal["redis", "rate_limit", "messaging"]
    operation: Literal["connect", "check", "enqueue", "process", "close"]


Event: TypeAlias = TaskEvent | LimitExceededEvent | BackendErrorEvent
EventCallback: TypeAlias = Callable[[Event], Awaitable[None]]


class EventDispatcher:
    """Await async callbacks sequentially in their registration order."""

    def __init__(self, callbacks: tuple[EventCallback, ...] = ()) -> None:
        self._callbacks = tuple(callbacks)

    async def emit(self, event: Event) -> None:
        for callback in self._callbacks:
            try:
                await callback(event)
            except Exception:  # noqa: BLE001 -- Observers must not break the main operation.
                logger.warning("Event callback failed")
