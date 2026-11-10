# Local recovery artifact

`taskiq_redis-1.2.4+aiogramext.2-py3-none-any.whl` is a local derivative of
Taskiq Redis 1.2.4 by the Taskiq team, distributed under MIT. The original license
is preserved inside the wheel. This artifact is not an official upstream release.

Runtime changes remove the empty-read early `continue` before reclaim and preserve
the reclaim cursor between scans; see [the patch](../recovery.patch). Version and wheel metadata identify the
local derivative. The wheel includes the patch, upstream URL/SHA-256 and modified
source SHA-256 in its distribution metadata. `transport-1.2.4+aiogramext.2.json` records the wheel
hash; the separate verified environment lock pins that same artifact.

Rebuild from the repository root:

```console
uv run --directory spikes/redis_queue --locked python build_transport.py --download
```

The builder verifies the pinned upstream hash and refuses to overwrite a different
existing artifact. It uses fixed ZIP metadata and no compression for reproducible
bytes across the supported Python interpreters. Publication is not part of the
spike. The main aiogram-extensions package does not depend on or bundle this wheel.
