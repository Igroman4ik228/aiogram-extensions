"""Queue task states and enqueue results."""

from dataclasses import dataclass
from enum import StrEnum

from aiogram_extensions._validation import ConfigurationError, nonempty_string


class TaskState(StrEnum):
    ENQUEUED = "enqueued"
    STARTED = "started"
    RETRY_SCHEDULED = "retry_scheduled"
    COMPLETED = "completed"
    FAILED = "failed"
    DLQ = "dlq"


@dataclass(frozen=True, kw_only=True)
class EnqueueResult:
    task_id: str
    generation: str
    duplicate: bool = False

    def __post_init__(self) -> None:
        nonempty_string("task_id", self.task_id)
        nonempty_string("generation", self.generation)

        if type(self.duplicate) is not bool:
            raise ConfigurationError("duplicate", "must be a boolean")
