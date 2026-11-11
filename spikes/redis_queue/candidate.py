"""Build a disposable source overlay; never mutate installed dependencies."""

import difflib
import hashlib
import shutil
from importlib.metadata import distribution
from pathlib import Path

SOURCE_SHA256 = "73d4603b3963271e77c82db1240571904abfd66bbc208f35326ea1b993348d6e"
REMOVED = "                if not fetched:\n                    continue\n"


def patched_source(original: bytes) -> bytes:
    if hashlib.sha256(original).hexdigest() != SOURCE_SHA256:
        raise ValueError("Unexpected taskiq-redis source")  # noqa: TRY003 -- Artifact guard.

    text = original.decode()
    updated = text.replace(REMOVED, "", 1)
    updated = (
        updated.replace(
            '            while True:\n                logger.debug("Starting fetching new messages")',
            "            reclaim_cursors: dict[str, str | bytes] = {}\n"
            '            while True:\n                logger.debug("Starting fetching new messages")',
            1,
        )
        .replace(
            "                        min_idle_time=self.idle_timeout,\n",
            "                        min_idle_time=self.idle_timeout,\n"
            '                        start_id=reclaim_cursors.get(stream, "0-0"),\n',
            1,
        )
        .replace(
            "                    pending = results[1]\n",
            "                    pending = results[1]\n"
            "                    reclaim_cursors[stream] = pending[0]\n",
            1,
        )
    )

    patch = "".join(
        difflib.unified_diff(
            text.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile="a/taskiq_redis/redis_broker.py",
            tofile="b/taskiq_redis/redis_broker.py",
        )
    )

    if patch != Path(__file__).with_name("recovery.patch").read_text(encoding="utf-8"):
        raise ValueError("Recovery patch mismatch")  # noqa: TRY003 -- Reviewed patch must match.

    return updated.encode()


def prepare_candidate(destination: Path) -> Path:
    package = distribution("taskiq-redis")
    if package.version != "1.2.4":
        raise ValueError(package.version)

    source = Path(str(package.locate_file("taskiq_redis")))
    updated = patched_source((source / "redis_broker.py").read_bytes())

    target = destination / "taskiq_redis"
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    broker_file = target / "redis_broker.py"
    broker_file.write_bytes(updated)

    return broker_file
