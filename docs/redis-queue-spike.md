# Redis queue technical spike

Date: 10 November 2026. Scope: roadmap stage 1. This establishes a stack and
application protocol for later implementation, not a production messaging API.
See [source findings](spike-sources.md), [durability assumptions](spike-durability-sources.md)
and [earlier experiments](redis-queue-spike-history.md).

## Stack decision

Use Taskiq 0.13.0 with the immutable local Taskiq Redis derivative
`1.2.4+aiogramext.2`, aiogram 3.31.0 and redis-py 8.1.0 for the verified spike.
The unmodified Taskiq Redis 1.2.4 remains unsuitable for autonomous idle recovery.
The current patch fixes both the idle-read skip and reclaim cursor starvation.

The durable application layer demonstrates persistent attempts/deadlines,
idempotent DLQ/replay, an enqueue outbox and renewable ownership using public
Taskiq enqueue/worker/lifecycle/manual-ACK APIs. It does not replace Taskiq's
worker engine or Redis transport. This is a conditional technical stack decision
for the tested standalone Redis profile and documented duplicate semantics.
Public API design, packaging for users and release approval belong to later stages.

## Reproduce the final profile

Requires uv and a running Docker Linux-container engine. From the repository root:

```console
uv sync --directory spikes/redis_queue --locked
uv run --directory spikes/redis_queue --locked python build_transport.py --download
uv sync --directory spikes/redis_queue/verified --locked
uv run --directory spikes/redis_queue/verified --locked pytest -c ../pyproject.toml ../tests --run-redis --durable --transport-wheel -q
uv run --directory spikes/redis_queue --locked pyright
```

The wheel is already included. The rebuild command also caches the pinned upstream
wheel for the offline reproducibility test. It downloads only when absent, verifies
the upstream hash, and refuses to replace a different existing artifact.

For another interpreter and machine-readable local evidence:

```console
uv run --directory spikes/redis_queue/verified --isolated --python 3.11 --locked pytest -c ../pyproject.toml ../tests --run-redis --durable --transport-wheel --junitxml=../.venv/results/hardened311.xml -q
```

Replace `3.11`/`311` with the required interpreter. The verified project installs
the wheel into its own environment without runtime source patching. Its lock pins
the exact wheel hash. The original spike environment remains available for comparison:

```console
uv run --directory spikes/redis_queue --locked pytest --run-redis -q
uv run --directory spikes/redis_queue --locked pytest -q
```

Without `--run-redis`, no Redis container starts. Service tests skip explicitly;
the rebuild test skips only when its upstream cache is missing. Direct transport
tests require the installed `--transport-wheel`. The historical source overlay
remains opt-in via `--recovery-candidate` for comparison.

Tests use disposable named containers, loopback-only ephemeral ports, random key
namespaces and a local Telegram HTTP API. Redis kill/restart/pause affects only the
fixture-owned container. Teardown removes its anonymous data volume. No real bot
credentials or existing Redis data are needed.

## Versions and artifact

| Component | Verified profile |
| --- | --- |
| Host | Windows x64; Docker Desktop Linux engine 28.5.1 |
| Python | CPython 3.11.18 / 3.12.11 / 3.13.7 / 3.14.10 |
| aiogram / Taskiq / redis-py | 3.31.0 / 0.13.0 / 8.1.0 |
| Taskiq Redis | Local `1.2.4+aiogramext.2`, not an official upstream release |
| Redis | 7.2.5 Alpine, image `sha256:6aaf3f5e6bc8a592fbfe2cccf19eb36d27c39d12dab4f4b01556b7449e7b1f44` |
| Persistence | AOF enabled, `appendfsync always`, snapshots disabled, `noeviction`; same volume on restart |
| Broker | Group ID `0`, read count 1, block 100 ms, reclaim lock timeout 1 s; no trimming |
| Recovery tests | Idle threshold 200 ms; normal tasks otherwise use 5000 ms |
| Ownership | Random owner token, lease 3000 ms, renewal every 500 ms; separate application/broker pools |
| Application Redis | Decoded responses, read/connect timeout 1 s; no automatic command retries |
| Policy | Three reserved attempts; 256 KiB JSON payload; default terminal retention one day |

The [artifact notice](../spikes/redis_queue/artifacts/NOTICE.md) identifies the MIT
derivative and rebuild procedure. Wheel SHA-256:
`588f693e017174deb3d2710308e05011ef9b0c507b3aefad464567d94dcaa6f4`.
Modified source SHA-256:
`0569fb4baf7b4a0376eeb96eb50e355530e0ca4c7453c4567dbb8da102e3bef3`.

The wheel embeds [the complete patch](../spikes/redis_queue/recovery.patch), upstream
URL/hash, modified source hash and unchanged license. Tests rebuild it byte for byte,
check every RECORD entry and ensure all unpatched runtime files remain unchanged.
Producer and worker check the imported source hash. The root package/lock and original
spike dependency lock are preserved; only the verified environment depends on the fork.

## Delivery and recovery protocol

[`journal.lua`](../spikes/redis_queue/journal.lua) checks key types and the complete
journal command/key ACL set before any mutation. Lua prevents interleaving, but does
not roll back runtime errors. Retention accepts strict integers from 1 ms through
365 days; invalid values fail before Redis I/O. The protocol requires the tested
standalone Redis command set and fully authorized Taskiq stream operations.

1. Enqueue commits immutable JSON, a unique generation and an outbox member in one
   script. Duplicate enqueue returns that generation. Conflicts and oversized
   payloads are rejected. Active state has no expiry.
2. Producer or worker dispatcher publishes through public `kiq()`, then removes the
   outbox member. A crash can duplicate publication; generation/status/ownership
   checks absorb duplicate Taskiq envelopes in the tested windows.
3. A worker claims and renews a random-token lease. Another worker finding it busy
   leaves the original delivery pending. Lost ownership/backend failure cancels
   the owned operation; journal mutations require its token.
4. Every attempt is reserved before HTTP. Attempts and Redis-time retry deadlines
   survive death. A crash before HTTP consumes a reservation; exhaustion produces
   a durable DLQ outcome rather than silently discarding the job.
5. Failure classification persists before DLQ handoff. DLQ append, expiry index and
   terminal state change in one script. A failed handoff leaves failure pending;
   retrying it does not resend HTTP.
6. Success persists before manual ACK. Redelivery after that commit only ACKs;
   redelivery after Telegram success but before the commit can duplicate the send.
7. Replay atomically removes the selected DLQ entry, creates one new generation,
   resets attempts and records outbox work. Repeating that replay returns the same
   generation. Old pending deliveries cannot execute it.

Terminal metadata expires after retention. DLQ payload/index cleanup runs in
batches of at most 100 in the worker loop or via `journal.cleanup()`. If cleanup
stops, expired DLQ bodies remain until it resumes. Active jobs, pending transport
messages and uncommitted outbox work are not expired or trimmed by cleanup.
Stream retention/backpressure design remains a production requirement.

## Checked scenarios

Tests exercise real processes, Redis and HTTP, covering all required send methods,
file IDs, URLs and shared paths, plus:

| Boundary | Observation required by assertions |
| --- | --- |
| Idle restart / three crash points | Recovery without fresh traffic; eventual pending ACK |
| Active pending prefix | Cursor advances to an eligible abandoned tail |
| Reclaim batch | Five entries recover with batch size two |
| Concurrent producer / unknown publication outcome | One logical job; repeated envelopes yield one HTTP effect in tested lease/completed-state windows |
| Producer death after journal commit | Outbox survives and worker dispatches it |
| Retry / retry_after restart | Attempt budget and server-time deadline survive |
| Permanent failure / failed DLQ write | Persistent failure decision, one DLQ entry, no unnecessary HTTP resend |
| Replay producer death / repeat replay | One generation survives; stale pending task cannot execute it |
| Long live task / competing workers | Renewal protects the tested task beyond initial lease and idle threshold |
| Worker and Redis SIGKILL | Same-volume AOF restores pending work and attempts |
| Redis pause | Blocked owned work cancels before HTTP; recovery resumes after restoration |
| Retention, payload/config and ACL errors | Active jobs survive cleanup; rejected input/rights leave no partial enqueue |
| Lifecycle / packaging | Graceful Bot close; clean installed fork with verified RECORD/provenance |

## Guarantee boundaries

This profile supports recoverable processing with bounded attempts and durable
terminal outcomes in the tested failures. It does not promise exactly-once
Telegram delivery. Telegram has no journal lease/fencing token: an unknown response,
process suspension beyond expiry or in-flight request when ownership changes can
still duplicate an external effect. Cancellation cannot retract it. The suite
explicitly preserves the success-before-commit duplicate case.

Persistence depends on the OS/storage honoring fsync and retaining the volume.
Untested: host/storage failure, full disk, sustained memory pressure, ACL changes
across operations, Cluster/Sentinel/replica failover, arbitrary partitions and
non-Windows worker hosts. No silent Memory fallback or unconditional parallel-send
guarantee is claimed. Production timeouts, backpressure, public API validation and
operational policies remain later-stage work.

## Verification evidence

Each journal behavior followed a focused failing test and minimal implementation.
The first installed candidate failed the new cursor regression; the second artifact
passed it. Invalid retention initially accepted all three bad inputs. The ACL test
reproduced a stranded job without outbox. Both regressions pass after validation
and permission preflight. Earlier cycles are retained in the historical report.

The final full suite passed in clean isolated environments using the verified lock:

| Python | Result | Runtime |
| --- | --- | --- |
| 3.11.18 | 51 passed, no skips | 345.74 s |
| 3.12.11 | 51 passed, no skips | 357.64 s |
| 3.13.7 | 51 passed, no skips | 360.02 s |
| 3.14.10 | 51 passed, no skips | 351.11 s |

Each run contains 30 E2E, 16 integration and 5 unit cases. Local JUnit evidence is
`.venv/results/accepted311.xml` through `accepted314.xml`. The unmodified 3.14
comparison passed 35 cases with 16 explicit durable/candidate skips in 210.83 s;
its crash tests still require trigger traffic. The default offline invocation
passed 8 cases with 43 explicit service skips.

An earlier matrix exposed an incorrect assumption in two tests: outbox publication
can produce multiple transport envelopes for one job. Those tests now require
conservation of every observed pending ID across the fault, eventual recovery, one
DLQ record and the expected HTTP count. The application behavior was preserved;
the final complete matrix above includes these stronger checks.

`uv run --locked ruff check .`, `uv run --locked ruff format --check .`,
`uv run --locked pyright`, separate spike Pyright, and `uv run --locked pytest`
(four main tests) passed. `uv build` succeeded; wheel/sdist inspection confirmed
the current README, license, typing marker and unchanged base aiogram requirement.
The normal packaging test installed the wheel into a clean environment. The
verified environment installed the local fork normally from its hash-pinned lock.
The fork rebuilt with the same hash and valid RECORD on every interpreter.

README command blocks match across translations; local documentation links,
new-file whitespace and `git diff --check` passed. The main package, root lock,
original spike dependency lock, examples, existing tests, AGENTS.md, TZ.md and
ROADMAP.md are preserved. All experiment-owned Redis containers/volumes were
removed. Remote CI and live Telegram were not run.

## Stage 1 acceptance

| Roadmap requirement | Evidence |
| --- | --- |
| Pinned producer → RedisStreamBroker → worker → controlled API and Python compatibility | Verified wheel/lock, real CLI processes, complete 3.11–3.14 matrix |
| Hard kill, pending/reclaim, idle/long-task/multi-worker behavior | Recovery, cursor, lease, pause and same-volume Redis crash tests; duplicate case retained explicitly |
| Retry / retry_after / limit / DLQ / replay ownership | Persistent application journal and idempotent outbox/terminal transitions; one policy owner, manual ACK |
| Bot factory, lifecycle, pool separation and required serialization | Worker-only Bot startup, graceful close, required-method/reference cases through the durable layer |
| Reproduction, observed guarantees, limits and stack decision | This report, historical evidence, primary-source notes and checked-in immutable artifact |

Stage 1 is complete for this declared experiment profile. Contract design can
proceed using this pinned stack and protocol. This is not approval to publish or
claim completion of the production Redis queue/MVP.
