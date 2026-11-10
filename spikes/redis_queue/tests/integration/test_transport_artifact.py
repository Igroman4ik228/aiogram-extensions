"""Verify the immutable fork artifact, its provenance and wheel RECORD."""

import base64
import csv
import hashlib
import io
import zipfile
from pathlib import Path

import pytest
from build_transport import FILENAME, build

ROOT = Path(__file__).resolve().parents[2]


def test_rebuild_is_deterministic_and_preserves_unpatched_files() -> None:
    path = ROOT / ".venv/upstream-taskiq-redis.whl"
    if not path.is_file():
        pytest.skip("Cache the pinned upstream wheel with build_transport.py --download")
    upstream = path.read_bytes()

    first = build(upstream)
    second = build(upstream)

    assert first == second
    assert first == (ROOT / "artifacts" / FILENAME).read_bytes()
    with (
        zipfile.ZipFile(io.BytesIO(first)) as wheel,
        zipfile.ZipFile(io.BytesIO(upstream)) as original,
    ):
        for name in original.namelist():
            if name.startswith("taskiq_redis/") and not name.endswith(("/", "redis_broker.py")):
                assert wheel.read(name) == original.read(name)
        assert wheel.read(
            "taskiq_redis-1.2.4+aiogramext.2.dist-info/licenses/LICENSE"
        ) == original.read("taskiq_redis-1.2.4.dist-info/licenses/LICENSE")


def test_committed_wheel_has_valid_metadata_and_records() -> None:
    with zipfile.ZipFile(ROOT / "artifacts" / FILENAME) as wheel:
        metadata = wheel.read("taskiq_redis-1.2.4+aiogramext.2.dist-info/METADATA").decode()
        assert "Version: 1.2.4+aiogramext.2\n" in metadata
        record_name = "taskiq_redis-1.2.4+aiogramext.2.dist-info/RECORD"
        rows = list(csv.reader(io.StringIO(wheel.read(record_name).decode())))
        assert {row[0] for row in rows} == set(wheel.namelist())
        for name, digest, size in rows:
            if name == record_name:
                assert digest == size == ""
            else:
                data = wheel.read(name)
                expected = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
                assert digest == "sha256=" + expected.decode()
                assert size == str(len(data))


def test_builder_rejects_unknown_upstream_artifact() -> None:
    with pytest.raises(ValueError, match="76e3621517a3c285bc9b72830cb7fa065"):
        build(b"not-the-pinned-upstream-wheel")
