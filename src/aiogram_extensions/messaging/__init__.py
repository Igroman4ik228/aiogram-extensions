"""In-process message queue and shared delivery contracts."""

from typing import TYPE_CHECKING

from .config import (
    QueueConfig,
    RetryConfig,
)
from .errors import (
    DeadLetterNotFoundError,
    DrainTimeoutError,
    IdempotencyConflictError,
    QueueBackendError,
    QueueError,
    QueueFullError,
    QueueNotRunningError,
    SerializationError,
)
from .models import (
    EnqueueResult,
    TaskState,
)

if TYPE_CHECKING:
    from .queue import DeadLetter, MessageQueue
    from .retry import FailureReason, RetryPolicy

__all__ = [
    "DeadLetter",
    "DeadLetterNotFoundError",
    "DrainTimeoutError",
    "EnqueueResult",
    "FailureReason",
    "IdempotencyConflictError",
    "MessageQueue",
    "QueueBackendError",
    "QueueConfig",
    "QueueError",
    "QueueFullError",
    "QueueNotRunningError",
    "RetryConfig",
    "RetryPolicy",
    "SerializationError",
    "TaskState",
]


def __getattr__(name: str) -> object:
    if name in {"DeadLetter", "MessageQueue"}:
        from . import queue  # noqa: PLC0415

        return getattr(queue, name)
    if name in {"FailureReason", "RetryPolicy"}:
        from . import retry  # noqa: PLC0415

        return getattr(retry, name)

    raise AttributeError(name)
