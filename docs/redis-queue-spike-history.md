# Redis queue spike: historical experiments

This file records the earlier unmodified transport and first candidate results.
The current decision, artifact and guarantees are in the
[completed-stage report](redis-queue-spike.md). Statements below describe their
historical configurations; the final stage uses a newer patch and durable layer.

Date: 10 November 2026. Scope: roadmap stage 1, before introducing a library
queue API or Redis extras. See the [primary-source review](spike-sources.md).

## Decision

**NO-GO for the production Redis queue with the unmodified pinned transport.**
An idle restarted worker does not reclaim pending tasks until new traffic arrives.
All three hard-kill experiments reproduce this. Passing characterization tests
establish the defect, not the stage 1 exit criterion or at-least-once delivery.

Keep Taskiq/Redis as experimental candidates. Before production work, agree on an
upstream recovery fix and verified pinned release/fork, or a change to the
stack/specification. A heartbeat producer or replacement transport must not be
introduced silently. `TZ.md` and `ROADMAP.md` remain unchanged.

Public Taskiq lifecycle and manual ACK APIs support the tested Bot factory and
experimental retry/DLQ layer. They do not resolve recovery or make handoffs atomic.
The [recovery candidate](#recovery-candidate) below tests a minimal upstream source
fix. It does not change the NO-GO conclusion for the unmodified release.

## Reproduce

From the repository root, with uv and a running Docker Linux-container engine:

```console
uv sync --directory spikes/redis_queue --locked
uv run --directory spikes/redis_queue --locked pytest --run-redis -q
uv run --directory spikes/redis_queue --locked pyright
```

The fixture starts a disposable Redis container with a random name and ephemeral
loopback port, and removes that specific container on teardown. It pulls the pinned
image if absent. No existing Redis data is read or removed. The local HTTP API also
uses an ephemeral loopback port; no Telegram credentials or Telegram access are
needed. Producer and actual Taskiq CLI workers run in separate processes.

Without `--run-redis`, five offline payload tests run and service tests explicitly
skip. The main package suite remains independent. Its normal Ruff, Pyright and
pytest commands still apply, and Ruff includes the spike files.

For another Python version and machine-readable local evidence:

```console
uv run --directory spikes/redis_queue --isolated --python 3.11 --locked pytest --run-redis --junitxml=.venv/results/python311.xml -q
```

Replace `3.11` with `3.12`, `3.13` or `3.14` as needed. JUnit files are ignored
local output, not portable delivery guarantees.

## Pinned environment

| Component | Experiment value |
| --- | --- |
| Host | Windows x64; Docker Desktop Linux engine 28.5.1 |
| Python | CPython 3.11.18, 3.12.11, 3.13.7, 3.14.10 |
| aiogram / Taskiq / taskiq-redis / redis-py | 3.31.0 / 0.13.0 / 1.2.4 / 8.1.0 |
| Resolution | Separate `spikes/redis_queue/uv.lock`; base `uv.lock` preserved |
| Redis | 7.2.5 Alpine; digest `sha256:6aaf3f5e6bc8a592fbfe2cccf19eb36d27c39d12dab4f4b01556b7449e7b1f44` |
| Redis persistence | AOF and snapshots disabled; server-crash durability untested |
| Stream/group | Per-test namespace, initial ID `0`, no trimming, read count 1, block 100 ms |
| Reclaim | Idle threshold 200 ms in crash/long-task tests, otherwise 5000 ms; lock timeout 1 s |
| Concurrency | One child per CLI supervisor; two independent supervisors for long-task test |
| ACK | Manual `Context.ack()` for delivery, stock default for stock retry |
| Resources | Broker-owned pool, worker-owned application client, independent observer client |

The worker command is equivalent to:

```console
uv run --directory spikes/redis_queue --locked taskiq worker tasks:broker --workers 1 --max-async-tasks 4 --max-prefetch 0 --log-level WARNING
```

The fixture supplies `SPIKE_REDIS_URL`, `SPIKE_PREFIX`, `SPIKE_API_URL` and optional
`SPIKE_IDLE_MS`. The producer runs `tasks.py` in another interpreter with
`SPIKE_TASK` and JSON `SPIKE_KWARGS`. These are test tools, not installed APIs.

## Observations

Assertions live in [E2E tests](../spikes/redis_queue/tests/e2e/test_delivery.py)
and [payload tests](../spikes/redis_queue/tests/unit/test_payload.py).

| Experiment | Observed result |
| --- | --- |
| Producer before worker startup | Message delivers; producer never runs Bot startup. ACK clears pending. |
| Required methods and media | Text/location/contact and photo/document/video/audio/media group all reach HTTP. Each media-bearing method is checked with file ID, URL and shared path: 18 cases in total. Uploads contain expected bytes. |
| Kill before send | One entry remains pending. Idle restart stalls. A new trigger task enables reclaim; original message then sends once. |
| Kill during HTTP processing | Pending survives and idle restart stalls. After a trigger, the original message has two HTTP requests; the first may already have had a side effect. |
| Kill after success, before ACK | Pending survives and idle restart stalls. Triggered reclaim sends a duplicate. |
| Live task above idle threshold | Two live worker processes execute and send the same job. No demonstrated heartbeat/lease extension. |
| Dropped connection / HTTP 500 | Experimental bounded policy retries and succeeds on attempt two. |
| HTTP 429 | Experimental policy waits at least the returned one second. |
| Permanent HTTP 400 | One attempt, DLQ record with exception class/count, then ACK. |
| Three HTTP 500 responses | Three total attempts, DLQ and ACK; no fourth send. |
| DLQ replay | Recorded JSON is re-enqueued, delivered, then removed from DLQ. |
| Failed DLQ write | Wrong-type Redis key makes persistence fail; original stays pending without ACK. |
| Stock SimpleRetryMiddleware | Three 429 executions occur in less than the requested one-second wait, then ACK without DLQ. |
| Graceful shutdown | Public signal handler drains and runs shutdown; HTTP session is checked closed before completion is recorded. Independent observer Redis client stays usable. Hard kill bypasses hooks. |
| Payload guards | BufferedInputFile and unknown schema version rejected; text contains no live Bot/default objects. |

Idle-recovery tests wait three idle thresholds and six read-block intervals before
the trigger. The tagged source also confirms `if not fetched: continue` before
`XAUTOCLAIM`; the conclusion does not rely solely on a bounded sleep.

## Responsibilities and limits

- Taskiq owns execution/injection; RedisStreamBroker owns delivery, pending and
  reclaim. Neither engine nor transport is replaced.
- One experimental delivery task owns classification, attempts, delay, DLQ write
  and ACK. Stock retry runs only on a separate comparison task.
- Retry delay is asynchronous sleep inside the task, not durable scheduling.
  Attempt count resets after process death. Production needs persisted attempts,
  backoff and a duration bound below the reclaim threshold.
- DLQ write then ACK and replay enqueue then delete are non-atomic. Crash windows
  can duplicate records/replays. Production needs task identity, idempotent durable
  handoffs, TTL/retention/cleanup and a public DLQ API. The spike has no DLQ TTL.
- ACK does not delete stream payload. Retention must preserve unfinished work;
  backpressure and stream trimming guarantees are not established.
- Wire version 1 and explicit local-file descriptors establish feasibility, not a
  stable schema. Worker defaults apply where producer defaults were omitted.
  Paths are trusted test inputs; production needs access/size/validation rules,
  schema evolution and complete runtime-object rejection.
- Untested: Redis failover/restart, network partitions, malformed/unknown Taskiq
  tasks, orphaned-lock crash windows, reclaim cursor traversal past ineligible
  entries, other aiogram versions and non-Windows worker hosts.
- Each process owns its Bot/client lifecycle. No live Bot or physical pool is
  shared between producer and workers. Hard kill cannot run graceful cleanup.

## Initial verification record

The text-payload and decoder tests first failed with `NotImplementedError`, and
the shared-file test first failed with a serialization error. Each passed after
its minimal implementation. Buffered-file rejection exposed a TypeError, now
normalized to ValueError at the producer boundary. An initial collection/import
error was corrected and was not counted as a valid red step.

The first E2E run had 12 passes and one long-task failure. Worker logs traced it
to a timed-out blocking read in the test barrier. Bounded one-second reads within
a total asynchronous timeout made the focused test pass with the same delivery
assertions. This corrected the harness, not the upstream recovery behavior.

The original 19-case suite passed on all four interpreters with the separate lock:

| Python | Result | Runtime |
| --- | --- | --- |
| 3.11.18 | 19 passed | 178.90 s |
| 3.12.11 | 19 passed | 182.41 s |
| 3.13.7 | 19 passed | 185.16 s |
| 3.14.10 | 19 passed | 177.22 s |

Each run includes five offline and fourteen E2E cases. The matrix used the command
above with the corresponding interpreter and JUnit output path. This validates
only the pinned spike stack, not every supported aiogram combination.

The three initial red/green slices used
`uv run --project spikes/redis_queue --locked pytest spikes/redis_queue/tests/unit/test_payload.py -q`:
text (one failure → one pass), local file (one failure → two passes), decoder
(one failure → three passes). The remaining payload guards were checked in the
interpreter matrix.

Final repository checks: `uv run --locked ruff check .`,
`uv run --locked ruff format --check .`, `uv run --locked pyright`,
`uv run --locked pytest` (four passed), and separate spike Pyright all passed.
The isolated default run produced five passes and fourteen explicit service
skips. After adding producer-timeout cleanup, the affected producer/worker smoke
test passed again; no delivery logic changed after the full matrix.

`uv build` produced wheel and sdist. Archive inspection confirmed the updated
package README, MIT metadata, typing marker and unchanged base aiogram requirement;
spike dependencies are absent from the wheel. The normal packaging test installed
the wheel into a clean environment. Local Markdown links and `git diff --check`
passed. Redis containers created by the experiments were removed. No remote CI or
live Telegram checks were run.

## Recovery candidate

The continued experiment removes only the empty-read early `continue` in
`RedisStreamBroker.listen()`. This lets the existing `XAUTOCLAIM` block run after
an idle `XREADGROUP` timeout. Group position, lock, Taskiq worker engine, retry
policy and ACK implementation are unchanged. Compare the
[reviewable two-line patch](../spikes/redis_queue/recovery-idle.patch) against the
[tagged source](https://github.com/taskiq-python/taskiq-redis/blob/1.2.4/taskiq_redis/redis_broker.py).
The official [release list](https://github.com/taskiq-python/taskiq-redis/releases)
still lists 1.2.4 as latest when checked on 10 November 2026.

Reproduce from the repository root:

```console
uv run --directory spikes/redis_queue --locked pytest --run-redis --recovery-candidate -q
```

The explicit flag makes the fixture copy the installed package into a test-owned
temporary directory. It requires version 1.2.4 and source SHA-256
`73d4603b3963271e77c82db1240571904abfd66bbc208f35326ea1b993348d6e`, and verifies
that its generated diff exactly matches the checked-in patch. Producer and worker
processes receive the copy through `PYTHONPATH` and check the actual broker import
path before starting. Installed files and both lockfiles remain untouched. The
[overlay builder](../spikes/redis_queue/candidate.py) introduces no new dependency,
runtime monkeypatch or alternative worker/transport implementation.
The resulting source SHA-256 is
`db8f435d11420da24919277734b49cf06e72e74bc6f46b836011e248495c0044`.
Direct artifact checks confirmed that installed source bytes remain unchanged;
unexpected version/source/patch checks each raised ValueError before copying.

The initial regression command was:

```console
uv run --directory spikes/redis_queue --locked pytest tests/e2e/test_recovery.py --run-redis --recovery-candidate -q
```

Before the overlay implementation it failed: the restarted worker made no HTTP
request and completion timed out after ten seconds (one failure, 21.64 s overall).
After the two-line change the same command passed (one pass, 11.30 s). This isolates
the cause from group-position and lock hypotheses; neither was changed.

Six targeted scenarios then passed in 67.39 s: all three hard-kill points recover
without trigger traffic, the minimal idle regression recovers, five pending jobs
drain with reclaim batch size two, and a task below its idle threshold is not
claimed by the other worker. The baseline branch retains its pending/stall/trigger
assertions; candidate mode instead requires autonomous completion. Explicit
candidate-only tests skip on the baseline, so a baseline pass never implies the
recovery contract passes.

The candidate still duplicates a live task after its idle threshold and can
duplicate a Telegram send after success before ACK. A task timeout must remain
below the reclaim threshold with operational margin; the patch is not a heartbeat
or an exactly-once mechanism. A permanently blocking read (`xread_block=0`) would
also prevent a periodic idle scan and is outside the tested configuration.

This establishes a concrete minimal recovery fix to carry in a reviewed immutable
upstream/fork artifact. The temporary overlay is test evidence, not an approved
production distribution. Durable attempts, idempotent DLQ/replay and the remaining
limitations above still belong to the production contract and implementation.

The complete candidate suite passed with the unchanged separate lock:

| Python | Candidate result | Runtime |
| --- | --- | --- |
| 3.11.18 | 22 passed | 241.17 s |
| 3.12.11 | 22 passed | 247.46 s |
| 3.13.7 | 22 passed | 249.64 s |
| 3.14.10 | 22 passed | 238.12 s |

Each run includes five offline and seventeen E2E cases. The corresponding JUnit
files are `.venv/results/candidate311.xml` through `candidate314.xml`.
The independent unmodified 3.14 run passed twenty cases with the two explicit
candidate contracts skipped in 219.80 s (`.venv/results/upstream314.xml`). Its
crash scenarios still assert the original stall before adding trigger traffic.

Ruff lint/format, main and spike Pyright, the four main tests, and wheel/sdist
build/inspection passed after the code changes. Current package README contents,
base dependency metadata and local documentation links were checked. The default
spike run passed five offline tests with seventeen explicit service skips. Both
locks and the installed dependency remain unchanged; no remote CI or live Telegram
checks were performed. Experiment-owned Redis containers were removed.
