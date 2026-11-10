"""Check that importing the installed package does not eagerly load dependencies."""

import subprocess
import sys


def test_base_import_is_independent() -> None:
    subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            (
                "import sys; import aiogram_extensions; "
                "assert not {'aiogram', 'redis', 'taskiq', 'taskiq_redis'} & sys.modules.keys()"
            ),
        ],
        check=True,
    )
