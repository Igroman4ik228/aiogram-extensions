"""Reproducible local wheel for the reviewed upstream recovery patch."""

import argparse
import base64
import csv
import hashlib
import io
import json
import urllib.request
import zipfile
from pathlib import Path

from candidate import patched_source

VERSION = "1.2.4+aiogramext.2"
FILENAME = f"taskiq_redis-{VERSION}-py3-none-any.whl"
UPSTREAM_SHA256 = "76e3621517a3c285bc9b72830cb7fa065be8213c08033025b4b13401eec9268e"
UPSTREAM_URL = (
    "https://files.pythonhosted.org/packages/2b/4c/"
    "d969bea733f1450e05af8738fb0eaf843852932901598c959ec82200d746/"
    "taskiq_redis-1.2.4-py3-none-any.whl"
)
ROOT = Path(__file__).resolve().parent


def build(upstream: bytes) -> bytes:
    if hashlib.sha256(upstream).hexdigest() != UPSTREAM_SHA256:
        raise ValueError(UPSTREAM_SHA256)

    old_info = "taskiq_redis-1.2.4.dist-info"
    new_info = f"taskiq_redis-{VERSION}.dist-info"
    with zipfile.ZipFile(io.BytesIO(upstream)) as archive:
        files = {
            name.replace(old_info, new_info, 1): archive.read(name)
            for name in archive.namelist()
            if not name.endswith(("/", "/RECORD"))
        }

    files["taskiq_redis/redis_broker.py"] = patched_source(files["taskiq_redis/redis_broker.py"])

    metadata = files[f"{new_info}/METADATA"]
    files[f"{new_info}/METADATA"] = metadata.replace(
        b"Version: 1.2.4\n",
        f"Version: {VERSION}\n".encode(),
        1,
    )
    files[f"{new_info}/WHEEL"] = (
        b"Wheel-Version: 1.0\nGenerator: aiogram-extensions-spike\n"
        b"Root-Is-Purelib: true\nTag: py3-none-any\n"
    )

    patch = (ROOT / "recovery.patch").read_text(encoding="utf-8").encode()
    files[f"{new_info}/recovery.patch"] = patch
    files[f"{new_info}/provenance.json"] = (
        json.dumps(
            {
                "upstream_url": UPSTREAM_URL,
                "upstream_sha256": UPSTREAM_SHA256,
                "patch_sha256": hashlib.sha256(patch).hexdigest(),
                "source_sha256": hashlib.sha256(files["taskiq_redis/redis_broker.py"]).hexdigest(),
                "version": VERSION,
            },
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode()

    record = io.StringIO(newline="")
    writer = csv.writer(record, lineterminator="\n")
    for name, data in sorted(files.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        writer.writerow([name, "sha256=" + digest, len(data)])
    writer.writerow([f"{new_info}/RECORD", "", ""])
    files[f"{new_info}/RECORD"] = record.getvalue().encode()

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)

    return output.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--download", action="store_true", help="Fetch pinned upstream if not cached"
    )

    args = parser.parse_args()
    cache = ROOT / ".venv/upstream-taskiq-redis.whl"
    if args.download and not cache.exists():
        with urllib.request.urlopen(UPSTREAM_URL, timeout=30) as response:
            original = response.read()
        if hashlib.sha256(original).hexdigest() != UPSTREAM_SHA256:
            raise ValueError(UPSTREAM_SHA256)

        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(original)

    artifact = build(cache.read_bytes())
    digest = hashlib.sha256(artifact).hexdigest()

    folder = ROOT / "artifacts"
    folder.mkdir(exist_ok=True)
    manifest = (json.dumps({"filename": FILENAME, "sha256": digest}, indent=2) + "\n").encode()

    for path, data in (
        (folder / FILENAME, artifact),
        (folder / f"transport-{VERSION}.json", manifest),
    ):
        if path.exists():
            if path.read_bytes() != data:
                raise FileExistsError(path)
        else:
            with path.open("xb") as target:
                target.write(data)

    print(f"{FILENAME}: sha256:{digest}")  # noqa: T201 -- Artifact verification output.


if __name__ == "__main__":
    main()
