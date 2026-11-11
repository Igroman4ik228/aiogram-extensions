"""Queue configuration contracts without workers or a transport."""

from typing import Any

import pytest

from aiogram_extensions.messaging import EnqueueResult, QueueConfig, RetryConfig, TaskState


@pytest.mark.parametrize("workers", [0, -1, True, 1.5])
def test_queue_rejects_invalid_worker_count(workers: int) -> None:
    with pytest.raises(ValueError, match="workers"):
        QueueConfig(workers=workers)


@pytest.mark.parametrize(
    ("settings", "error"),
    [
        ({"maxsize": 0}, "maxsize"),
        ({"drain_timeout": float("inf")}, "drain_timeout"),
        ({"enqueue_timeout": -1}, "enqueue_timeout"),
        ({"overflow": "drop"}, "overflow"),
        ({"max_payload_bytes": True}, "max_payload_bytes"),
        ({"idempotency_ttl": 0}, "idempotency_ttl"),
        ({"terminal_ttl": float("nan")}, "terminal_ttl"),
        ({"retry": None}, "retry"),
    ],
)
def test_queue_rejects_invalid_settings(settings: dict[str, Any], error: str) -> None:
    with pytest.raises(ValueError, match=error):
        QueueConfig(**settings)


@pytest.mark.parametrize(
    ("settings", "error"),
    [
        ({"max_attempts": 0}, "max_attempts"),
        ({"base_delay": -1}, "base_delay"),
        ({"max_delay": 0}, "max_delay"),
        ({"max_delay": 0.5}, "max_delay must be at least base_delay"),
        ({"retry_unknown": 1}, "retry_unknown"),
    ],
)
def test_retry_rejects_invalid_policy(settings: dict[str, Any], error: str) -> None:
    with pytest.raises(ValueError, match=error):
        RetryConfig(**settings)


def test_enqueue_result_distinguishes_a_duplicate_generation() -> None:
    result = EnqueueResult(task_id="job-1", generation="gen-2", duplicate=True)

    assert result.duplicate
    assert result.task_id == "job-1"
    assert result.generation == "gen-2"


def test_enqueued_state_has_stable_wire_value() -> None:
    assert TaskState.ENQUEUED == "enqueued"


@pytest.mark.parametrize(
    ("settings", "error"),
    [
        ({"task_id": ""}, "task_id"),
        ({"generation": " "}, "generation"),
        ({"duplicate": 1}, "duplicate"),
    ],
)
def test_enqueue_result_rejects_invalid_identity(settings: dict[str, Any], error: str) -> None:
    options: dict[str, Any] = {"task_id": "job-1", "generation": "gen-1"}
    options.update(settings)

    with pytest.raises(ValueError, match=error):
        EnqueueResult(**options)
