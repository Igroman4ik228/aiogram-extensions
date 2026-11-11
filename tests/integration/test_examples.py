"""Run offline examples as users would, outside the repository directory."""

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("script", "expected_lines"),
    [
        pytest.param(
            "package_info.py",
            ("Package: aiogram-extensions", "License: MIT", "Typing marker: present"),
            id="package-info",
        ),
        pytest.param(
            "bot_lifecycle.py",
            ("Session open: True", "Session closed: True"),
            id="bot-lifecycle",
        ),
        pytest.param(
            "contracts.py",
            (
                "Incoming rule: user_chat/fixed_window",
                "Queue capacity: 1000",
                "Event: task_enqueued",
            ),
            id="contracts",
        ),
        pytest.param(
            "pagination_core.py",
            ("Offset page: 2/2, items: Cherry", "Cursor page: Cherry", "Preview: A long ..."),
            id="pagination-core",
        ),
        pytest.param(
            "pagination_bot.py",
            ("Page 1/3", "Catalog", "Apple", "Banana", "Page 2/3", "Catalog", "Cherry", "Date"),
            id="pagination-navigation",
        ),
        pytest.param(
            "rate_limit.py",
            ("First update: True", "Blocked by: activity", "Another user: True"),
            id="rate-limit",
        ),
        pytest.param(
            "redis_lifecycle.py",
            (
                "Owned client: True",
                "Key: example:rate_limit:incoming:123:456",
                "Redis injected: True",
                "Manager closed: True",
            ),
            id="redis-lifecycle",
        ),
    ],
)
def test_example_runs_offline(
    script: str, expected_lines: tuple[str, ...], project_root: Path, tmp_path: Path
) -> None:
    example = project_root / "examples" / script

    result = subprocess.run(  # noqa: S603 -- Only repository-owned scripts from the cases above.
        [sys.executable, "-I", str(example)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert result.stdout.splitlines() == list(expected_lines)
    assert result.stderr == ""
