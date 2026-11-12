"""Errors reported by the message queue."""


class QueueError(Exception):
    """Base error for messaging operations."""


class QueueBackendError(QueueError):
    """The backend operation failed; acceptance or delivery may be unknown."""


class QueueNotRunningError(QueueError):
    """Enqueue requires a running queue."""


class QueueFullError(QueueError):
    """Capacity was reached or the enqueue deadline expired."""


class DrainTimeoutError(QueueError):
    """The shutdown deadline expired."""


class SerializationError(QueueError):
    """Unsupported, invalid or oversized wire payload."""


class IdempotencyConflictError(QueueError):
    """A retained idempotency key identifies a different payload."""


class DeadLetterNotFoundError(QueueError):
    """The requested failed generation is absent or its retention expired."""
