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
            "redis_lifecycle.py",
            (
                "Owned client: True",
                "Key: example:rate_limit:incoming:123:456",
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
