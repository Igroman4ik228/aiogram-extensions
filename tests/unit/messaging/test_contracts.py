"""Queue configuration contracts without workers or a transport."""

from typing import Any

import pytest

from aiogram_extensions.messaging import QueueConfig


@pytest.mark.parametrize("workers", [0, -1, True, 1.5])
def test_queue_rejects_invalid_worker_count(workers: int) -> None:
    with pytest.raises(ValueError, match="workers"):
        QueueConfig(workers=workers)


@pytest.mark.parametrize(
    "settings",
    [
        {"maxsize": 0},
        {"drain_timeout": float("inf")},
        {"enqueue_timeout": -1},
        {"overflow": "drop"},
        {"max_payload_bytes": True},
        {"idempotency_ttl": 0},
        {"terminal_ttl": float("nan")},
        {"retry": None},
    ],
)
def test_queue_rejects_invalid_settings(settings: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match=next(iter(settings))):
        QueueConfig(**settings)


@pytest.mark.parametrize(
    "settings",
    [
        {"max_attempts": 0},
        {"base_delay": -1},
        {"max_delay": 0},
        {"max_delay": 0.5},
        {"retry_unknown": 1},
    ],
)
def test_retry_rejects_invalid_policy(settings: dict[str, Any]) -> None:
    from aiogram_extensions.messaging import RetryConfig  # noqa: PLC0415

    with pytest.raises(ValueError, match=next(iter(settings))):
        RetryConfig(**settings)


def test_enqueue_result_distinguishes_a_duplicate_generation() -> None:
    from aiogram_extensions.messaging import EnqueueResult, TaskState  # noqa: PLC0415

    result = EnqueueResult(task_id="job-1", generation="gen-2", duplicate=True)
    assert result.duplicate
    assert result.generation == "gen-2"
    assert TaskState.ENQUEUED == "enqueued"
    with pytest.raises(ValueError, match="task_id"):
        EnqueueResult(task_id="", generation="gen-2")


@pytest.mark.parametrize("settings", [{"generation": " "}, {"duplicate": 1}])
def test_enqueue_result_rejects_invalid_identity(settings: dict[str, Any]) -> None:
    from aiogram_extensions.messaging import EnqueueResult  # noqa: PLC0415

    options: dict[str, Any] = {"task_id": "job-1", "generation": "gen-1"}
    options.update(settings)
    with pytest.raises(ValueError, match=next(iter(settings))):
        EnqueueResult(**options)
