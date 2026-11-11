"""Check that importing the installed package does not eagerly load dependencies."""

import subprocess
import sys
from textwrap import dedent


def test_base_import_is_independent() -> None:
    script = dedent(
        """
        import logging
        import sys

        root = logging.getLogger()
        handlers = root.handlers[:]
        level = root.level

        import aiogram_extensions
        import aiogram_extensions.integrations
        import aiogram_extensions.messaging
        import aiogram_extensions.pagination.core
        import aiogram_extensions.rate_limit
        import aiogram_extensions.redis
        import aiogram_extensions.utilities

        external_modules = {"aiogram", "redis", "taskiq", "taskiq_redis"}
        assert external_modules.isdisjoint(sys.modules)
        assert root.handlers == handlers
        assert root.level == level
        """
    )

    subprocess.run(  # noqa: S603 -- Fixed Python program, no external inputs.
        [sys.executable, "-I", "-c", script], check=True, timeout=30
    )


def test_missing_redis_extra_has_an_actionable_error() -> None:
    subprocess.run(  # noqa: S603 -- Fixed Python program, no external inputs.
        [
            sys.executable,
            "-I",
            "-c",
            dedent(
                """
                import asyncio
                import importlib.abc
                import sys

                class WithoutRedis(importlib.abc.MetaPathFinder):
                    def find_spec(self, fullname, path, target=None):
                        if fullname.split(".")[0] == "redis":
                            raise ModuleNotFoundError(name=fullname)
                        return None

                sys.meta_path.insert(0, WithoutRedis())
                from aiogram_extensions.redis import (
                    RedisConnectionManager, MissingRedisExtraError,
                )

                async def main():
                    manager = RedisConnectionManager()
                    try:
                        await manager.start()
                    except MissingRedisExtraError as error:
                        assert 'pip install "aiogram-extensions[redis]"' in str(error)
                    else:
                        raise AssertionError("Redis support was silently bypassed")
                    await manager.aclose()

                asyncio.run(main())
                """
            ),
        ],
        check=True,
        timeout=30,
    )
