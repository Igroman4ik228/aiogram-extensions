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
                import sys
                from importlib.metadata import distribution
                from importlib.resources import files
                from pathlib import Path

                import aiogram_extensions

                package_file = aiogram_extensions.__file__
                assert package_file is not None
                assert Path(package_file).resolve().is_relative_to(Path(sys.prefix).resolve())
                package = distribution("aiogram-extensions")
                assert package.metadata["License-Expression"] == "MIT"
                assert files("aiogram_extensions").joinpath("py.typed").is_file()
                """
            ),
        ]
    )
