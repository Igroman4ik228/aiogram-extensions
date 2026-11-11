"""Check the built wheel in a fresh environment, independent of the source tree."""

import shutil
import subprocess
import sys
from pathlib import Path
from textwrap import dedent


def test_installed_wheel_metadata_and_typing_marker(project_root: Path, tmp_path: Path) -> None:
    uv = shutil.which("uv")
    assert uv is not None, "uv must be available on PATH to test packaging"

    wheel_dir = tmp_path / "dist"
    environment = tmp_path / "venv"
    python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")

    def run(command: list[str], *, cwd: Path = tmp_path) -> None:
        result = subprocess.run(  # noqa: S603 -- Commands use uv, Python and local test artifacts.
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

        assert result.returncode == 0, result.stderr or result.stdout

    run([uv, "build", "--wheel", "--offline", "--out-dir", str(wheel_dir)], cwd=project_root)

    (wheel,) = wheel_dir.glob("*.whl")

    run([uv, "venv", "--offline", "--python", sys.executable, str(environment)])

    run([uv, "pip", "install", "--offline", "--no-deps", "--python", str(python), str(wheel)])

    run(
        [
            str(python),
            "-I",
            "-c",
            dedent(
                """
                import asyncio
                import sys
                from importlib.metadata import distribution
                from importlib.resources import files
                from pathlib import Path

                import aiogram_extensions
                from aiogram_extensions.rate_limit import RateRule
                from aiogram_extensions.messaging import QueueConfig
                from aiogram_extensions.integrations import TaskEvent
                from aiogram_extensions.redis import RedisConnectionManager, MissingRedisExtraError
                from aiogram_extensions.pagination import Paginator
                from aiogram_extensions.utilities import truncate_text

                package_file = aiogram_extensions.__file__
                assert package_file is not None
                assert Path(package_file).resolve().is_relative_to(Path(sys.prefix).resolve())

                package = distribution("aiogram-extensions")
                assert package.metadata["License-Expression"] == "MIT"
                assert files("aiogram_extensions").joinpath("py.typed").is_file()
                rate_limit = files("aiogram_extensions.rate_limit")
                assert rate_limit.joinpath("_check.lua").is_file()
                assert rate_limit.joinpath("_cooldown.lua").is_file()
                assert package.metadata.get_all("Provides-Extra") == ["redis"]
                requirements = package.metadata.get_all("Requires-Dist") or []
                assert any(
                    'redis==8.1.0' in item and 'extra == "redis"' in item.replace("'", '"')
                    for item in requirements
                )
                assert not any(item.startswith(("taskiq", "taskiq-redis")) for item in requirements)

                assert RateRule(name="incoming", limit=5, window=1).scope == "user_chat"
                assert QueueConfig().workers == 1
                assert TaskEvent(kind="task_enqueued", task_id="1", generation="1").attempt == 0
                assert "redis" not in sys.modules
                assert "aiogram" not in sys.modules
                assert Paginator([1, 2]).page_size == 10
                assert truncate_text("abcdef", max_units=3) == "ab…"

                async def check_missing_extra():
                    try:
                        await RedisConnectionManager().start()
                    except MissingRedisExtraError as error:
                        assert 'pip install "aiogram-extensions[redis]"' in str(error)
                    else:
                        raise AssertionError("The clean environment unexpectedly has Redis")

                asyncio.run(check_missing_extra())
                """
            ),
        ]
    )
