# Aiogram Extensions

[Русский](README_RU.md)

Modular extensions and utilities for aiogram 3. Planned features include rate
limiting, message queues, pagination and observability hooks.

**Under development:** validated configurations and results, typed callbacks and
a Redis connection manager are available. Pagination, limiter algorithms,
middleware and queue workers are not implemented yet.

## Getting started

Requires Python 3.11–3.14 and [uv](https://docs.astral.sh/uv/). The supported aiogram
range is `>=3.29.1,<3.32`. The base installation includes neither Redis nor Taskiq.
The optional `redis` extra pins redis-py 8.1.0; `messaging-redis` is deferred until
the production transport is packaged. Users can install with
`pip install aiogram-extensions` or `pip install "aiogram-extensions[redis]"`;
uv is only required by the development commands below.

From the repository root, install the package and development dependencies:

```console
uv sync --locked
```

Development dependencies include redis-py to test the optional API.

## Examples

Run these standalone scripts from the repository root using the locked project
environment:

```console
uv run --locked examples/package_info.py
uv run --locked examples/bot_lifecycle.py
uv run --locked examples/contracts.py
uv run --locked examples/redis_lifecycle.py
```

- [Package information](../examples/package_info.py): import the package and inspect
  its license and typing marker.
- [Session lifecycle](../examples/bot_lifecycle.py): open and close an aiogram HTTP
  session with a synthetic token, without sending Telegram API requests. This
  demonstrates the aiogram dependency, not an extension API.

- [Contracts and callbacks](../examples/contracts.py): configurations and an
  explicitly emitted event, without running a limiter or queue.
- [Redis ownership](../examples/redis_lifecycle.py): create/close a client and
  build a key; requires redis-py but no Redis server.

All four examples run without credentials, network access or external services.
Limiter and queue examples will accompany their implementations.
Examples are linted, type-checked and [tested as subprocesses](../tests/integration/test_examples.py).

## Stage 2 contracts

The following types and Redis lifecycle are implemented. Limiter and queue behavior
described here is the contract for stages 4–6, not a current runtime guarantee.
The package root exports no functional API. Import from functional modules:

```python
from aiogram_extensions.rate_limit import RateLimitConfig, RateLimitResult, RateRule
from aiogram_extensions.messaging import EnqueueResult, QueueConfig, RetryConfig, TaskState
from aiogram_extensions.integrations import EventDispatcher, TaskEvent
from aiogram_extensions.redis import RedisConfig, RedisConnectionManager

limits = RateLimitConfig(rules=(RateRule(name="incoming", limit=5, window=1),))
queue = QueueConfig(workers=2, maxsize=100, retry=RetryConfig(max_attempts=3))
```

### Configurations and results

Configurations are frozen, keyword-only standard dataclasses. Invalid settings
raise `ValueError`. Integer counts reject booleans/fractions. Durations are finite
positive seconds unless stated otherwise. Names, prefixes and namespaces accept
ASCII letters, digits, `_`, `.` and `-`.

| Type | Fields and defaults |
| --- | --- |
| `RateRule` | Required `name`, positive integer `limit`, positive `window`; `scope="user_chat"`, `algorithm="fixed_window"` (alternative: `sliding_window`). |
| `RateLimitConfig` | Required non-empty tuple `rules` with unique names; `on_limit="reject"` or `"wait"`. No implicit Telegram quotas. |
| `RateLimitResult` | Required boolean `allowed`; `retry_after=0`, `triggered_rules=()`. Denial requires a positive wait and blocking rule names; allowed results have neither. |
| `RetryConfig` | `max_attempts=3` including the first attempt; `base_delay=1`, `max_delay=60` (at least base), `retry_unknown=False`. |
| `QueueConfig` | `workers=1`, `maxsize=1000`, `overflow="wait"` or `"reject"`, `enqueue_timeout=None` (no deadline), `drain_timeout=30`, `max_payload_bytes=262144`, `idempotency_ttl=86400`, `terminal_ttl=86400`, `retry=RetryConfig()`. Counts must be positive; capacity is always bounded. |
| `EnqueueResult` | Required non-empty `task_id`, `generation`; `duplicate=False`. Confirms acceptance, not delivery. |
| `RedisConfig` | `url="redis://localhost:6379/0"` (hidden from repr), `prefix="aiogram_extensions"`, `connect_timeout=1`, `socket_timeout=1`, `max_connections=20`. Only `redis://`/`rediss://`, numeric databases, no URL query/fragment. |

### Limiter decisions

Keys isolate direction, rule name, algorithm and numeric bot ID (never its token).
All processes sharing a rule name/namespace must use identical settings; rename
the rule or namespace when changing settings. Additional scope identifiers:

| Scope | Identity and applicability |
| --- | --- |
| `user_chat` | User ID + chat ID. Incoming middleware skips events lacking either. |
| `bot` | No additional ID; shared across chats and methods. |
| `chat` | Chat ID, every chat type. |
| `group` | Chat ID only for explicitly confirmed `group`/`supergroup` types. Private/channel/unknown types do not match; negative IDs alone are insufficient. One budget per Telegram group, not across all groups. |
| `method` | Bot API method name, shared across chats. |
| `custom` | Explicit non-empty application key. |

Direct checks missing required user/chat/method/custom context raise `ValueError`.
Fixed Window starts at the first accepted event and uses `[start, start + window)`;
Sliding Window counts accepted events in `(now - window, now]`. Memory uses a
monotonic clock, Redis server `TIME`; one sample per decision. Expired state is
removed on access and in bounded cleanup; Redis additionally uses TTL.

Check/charge all applicable rules atomically. On rejection, charge none.
`triggered_rules` follows configuration order; `retry_after` is the maximum wait
among blocking rules. HTTP failure does not refund an admitted attempt. Reject
returns denial; wait asynchronously sleeps and rechecks, without reserving capacity
while asleep. Cancellation propagates; already admitted attempts remain counted.
Backend failure raises `RateLimitBackendError` (a `RateLimitError`), without a send
or fallback. Outgoing middleware records `TelegramRetryAfter` cooldown and re-raises
the error; the queue, or a standalone caller, owns HTTP retries. No nested retry
loop is introduced. Limits are application settings, not guaranteed Telegram quotas.

### Queue decisions

`TaskState`: enqueued → started → completed; transient failure uses
retry_scheduled → started. Exhaustion/permanent failure uses failed → dlq;
failed can persist while durable DLQ handoff is retried. Replay creates a new
enqueued generation with attempts reset; stale deliveries cannot execute it.

Memory workers start explicitly. Enqueue before start/after stop raises
`QueueNotRunningError`. `maxsize` bounds all accepted nonterminal jobs, including
active work and delayed retries; duplicates consume no slot. Reject mode raises
`QueueFullError`; wait mode awaits capacity up to `enqueue_timeout`, then raises
the same error. Cancellation before admission adds no job. Unknown Redis command
outcomes require checking acceptance by repeating the same idempotency key.

Memory first attempts dequeue in accepted FIFO order; Redis consumes ready entries
in stream publication order, which can differ from journal admission order.
Multiple workers/producers and retries provide no completion/per-chat ordering.
Stop rejects new admission and drains Memory up to `drain_timeout`; timeout cancels
and awaits workers, moves unfinished work into the in-memory DLQ, and raises
`DrainTimeoutError`. It cannot undo an HTTP effect. Stopping a Redis producer does
not drain remote workers; Taskiq owns their lifecycle and separate pool, retaining
unfinished durable work.

Planned Redis payload: UTF-8 JSON `{"version": 1, "method": "sendMessage", "params": {...}}`.
Measure the entire encoded envelope against `max_payload_bytes`; forbid non-finite
numbers. Unknown versions/methods, malformed/unsupported/oversized payloads raise
`SerializationError` before admission. Required methods: `sendMessage`, `sendPhoto`,
`sendDocument`, `sendVideo`, `sendAudio`, `sendMediaGroup`, `sendLocation`, `sendContact`.
Support file IDs, URLs and shared paths; `FSInputFile` becomes
`{"$local_file": path, "filename": name}`. The path must exist in the worker's
filesystem. Redis rejects buffered files, streams and live Bot/Message/Update/FSM
objects; Memory may retain local aiogram files. A worker creates its own Bot via
factory and closes only sessions it owns.

Within a backend/namespace, equal idempotency keys/payloads return the retained
task/generation with `duplicate=True`; different payloads raise
`IdempotencyConflictError`. Active identities never expire. After a terminal outcome,
`idempotency_ttl` retains identity/fingerprint, independently of `terminal_ttl` for
outcome/DLQ bodies. Expired bodies are inaccessible and removed by bounded worker
cleanup or explicit cleanup. Replay checks capacity and atomically replaces the
DLQ entry with one new generation; repeating a retained replay returns it. Memory
loses all state at process exit. Redis follows the [spike protocol](redis-queue-spike.md).

Reserve each attempt before HTTP. Retry transient network/server errors; permanent
errors go to DLQ, unknown errors retry only if configured. Default exponential
delay is capped at `max_delay`; `TelegramRetryAfter.retry_after` remains a minimum
even above that cap. Custom policies remain bounded by `max_attempts`. DLQ stores
task/generation, payload, classified reason and attempt count for public viewing/replay.
One layer owns retries/ACKs. `QueueBackendError` can mean acceptance/delivery is
unknown; all queue errors derive from `QueueError`. Unknown HTTP outcomes can
duplicate Telegram effects; no exactly-once guarantee is made.

### Redis lifecycle and events

`RedisConnectionManager(config=None, *, client=None)` borrows a client or creates
one lazily. Use `async with manager`, or `await manager.start()` and
`await manager.aclose()`. Start is idempotent, with no network I/O or health check;
commands on `manager.client` connect and propagate redis-py errors. Owned clients
use decoded responses, configured timeouts/pool size and zero automatic command
retries. Borrowed options remain unchanged. Prefix controls keys in both cases;
connection options apply only to owned clients. Taskiq keeps its separate pool.

Access before start/after close, or restart after close, raises `RedisLifecycleError`.
Close is idempotent, including concurrent calls; failed close can be retried.
Borrowed resources are never closed. Share one manager within one lifecycle;
nested contexts are unsupported. `key(namespace, *parts)` percent-encodes separate
non-empty string parts to prevent delimiter collisions. Imports/configuration/keys
need no Redis extra; creating an owned client without it raises `MissingRedisExtraError`
with `pip install "aiogram-extensions[redis]"`. Imports open no connections.
See the [official redis-py lifecycle API](https://redis.readthedocs.io/en/stable/examples/asyncio_examples.html).

Frozen events from `aiogram_extensions.integrations`:

| Event | Fields |
| --- | --- |
| `TaskEvent` | Required `kind` (`task_enqueued`, `task_started`, `task_completed`, `task_failed`, `retry_scheduled`, `task_dlq`, `dlq_replayed`), `task_id`, `generation`; `attempt=0`, `retry_after=0`. |
| `LimitExceededEvent` | Required `direction` (`incoming`/`outgoing`), tuple `triggered_rules`, `retry_after`. |
| `BackendErrorEvent` | Required `component` (`redis`/`rate_limit`/`messaging`), `operation` (`connect`/`check`/`enqueue`/`process`/`close`). |

`Event` is the union; `EventCallback` is an async callable accepting it and returning
`None`. `EventDispatcher(callbacks=())` snapshots callbacks; `await emit(event)`
awaits them in registration order. Ordinary exceptions produce a constant warning
in `aiogram_extensions.integrations.events`; remaining callbacks still run.
Exception text/tracebacks, IDs, credentials and payloads are not logged; root logging
is unchanged. Cancellation/other `BaseException` subclasses propagate. Slow callbacks
delay callers; applications can implement callback timeouts. Future components emit
after committing transitions; callback errors cannot roll them back. Cancellation
after commit may hide a result, so retry with idempotency. Events are local observations,
not a durable audit log. Currently applications emit explicitly; automatic component
events arrive with their implementations.

## Development

Run checks from the repository root:

```console
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pyright
uv run --locked pytest
```

Build a wheel and source distribution with `uv build`.

### Redis queue technical spike

Stage 1 has an isolated stack: an immutable patched transport wheel and a durable
application journal for attempts, ownership, enqueue outbox, DLQ and replay.
Base dependencies are unchanged. See the [report and guarantee boundaries](redis-queue-spike.md)
and [primary-source findings](spike-durability-sources.md).

With Docker's Linux engine running, execute from the repository root:

```console
uv sync --directory spikes/redis_queue --locked
uv run --directory spikes/redis_queue --locked python build_transport.py --download
uv sync --directory spikes/redis_queue/verified --locked
uv run --directory spikes/redis_queue/verified --locked pytest -c ../pyproject.toml ../tests --run-redis --durable --transport-wheel -q
uv run --directory spikes/redis_queue --locked pyright
```

Tests manage disposable Redis with AOF and a local HTTP API; no bot credentials
are needed. They include worker/Redis crashes and backend outages. Without
`--run-redis`, service tests skip explicitly. The verified environment installs
the pinned local wheel without runtime patching. These remain experimental tools
for later library implementation. Telegram delivery can still duplicate after
an unknown external outcome; exactly-once is not promised.

### TDD and test conventions

Tests automatically check expected behavior and catch regressions. Examples show
users how to use the public API and manage resources. Running an example does not
replace assertions about limits, errors or edge cases; testing examples keeps the
published usage instructions executable.

Develop features and bug fixes in small red-green-refactor cycles: add one test
for public behavior, run it and confirm the intended failure, implement the minimum
change, then refactor while keeping the tests green. For a regression, the test
must reproduce the bug first. Documentation and tooling changes use their relevant
checks instead of artificial failing tests.

Group tests first by level, then by feature as the suite grows:

| Level | Purpose | Location |
| --- | --- | --- |
| Unit | Isolated logic: algorithms, validation, pagination and retry decisions; no external services or processes. | `tests/unit/<feature>/` |
| Integration | Components working together: aiogram adapters, storage, package installation and example subprocesses. | `tests/integration/`, then feature subdirectories as needed |
| End-to-end (E2E) | A complete supported workflow, such as producer → Redis → worker → a local Telegram API stand-in. | `tests/e2e/<feature>/` |

The main package suite contains unit and integration tests. Add E2E directories
with their first tests. Current layout:

```text
tests/
├── conftest.py
├── unit/
│   ├── integrations/test_events.py
│   ├── messaging/test_contracts.py
│   ├── rate_limit/test_contracts.py
│   └── test_redis.py
└── integration/
    ├── test_examples.py
    ├── test_imports.py
    └── test_packaging.py
```

`conftest.py` provides the shared repository path. For future modules, use paths such
as `tests/unit/rate_limit/` and `tests/integration/rate_limit/`. A regression test
belongs to the affected feature and level; it is not a separate directory category.
Name tests `test_<behavior>`, annotate test and fixture functions, and separate setup,
action and assertions. Assert observable results, expected exceptions and relevant
edge cases through public interfaces; use independent expected values.

Use `pytest.mark.parametrize` for equivalent cases, with readable IDs. Keep fixtures
local until shared, then move them into the nearest `conftest.py`. Prefer built-in
fixtures such as `tmp_path`, `monkeypatch` and `capsys`; close resources with context
managers or yield fixtures. Mock external boundaries when necessary and keep the
default suite independent of real credentials and external services.

Async tests use `async def` directly: pytest-asyncio auto mode also handles async
`@pytest.fixture` fixtures. Tests and fixtures get function-scoped event loops.
Warnings and unexpected passes of `xfail` tests fail the suite. See the
[import test](../tests/integration/test_imports.py) for import isolation and the
[example tests](../tests/integration/test_examples.py) for parametrization and `tmp_path`.
The [packaging test](../tests/integration/test_packaging.py) builds a wheel with uv and checks
its license metadata and typing marker after installing it into a fresh virtual
environment. It runs offline, installs only the wheel without dependencies, and
requires uv with a compatible build backend available locally.

Useful commands from the repository root:

```console
uv run --locked pytest -q -x
uv run --locked pytest tests/integration/test_imports.py::test_base_import_is_independent -q
uv run --locked pytest tests/integration/test_examples.py -q
uv run --locked pytest -k lifecycle -q
uv run --locked pytest --lf -q
uv run --locked pytest --collect-only -q
```

These commands respectively run until the first failure, select one test, check
examples, filter by name, rerun the last failures, and list collected tests.

Run all integration tests with `uv run --locked pytest tests/integration -q`, or
only packaging with `uv run --locked pytest tests/integration/test_packaging.py -q`.

### Planned functional coverage

The following runtime checks will accompany implementations from the
[technical specification](TZ.md). Stage 2 already tests configurations, results,
callbacks, Redis ownership, independent imports and missing extras.

| Area | Behavior to verify |
| --- | --- |
| `rate_limit` | Fixed/Sliding Window boundaries, independent user/chat keys, combined rules, wait times, concurrent calls, Memory/Redis contract consistency and `TelegramRetryAfter`. |
| `messaging` | Supported payloads and serialization, bounded queues, declared ordering, retry decisions and attempt limits, DLQ/replay, idempotency, graceful shutdown and worker recovery after a crash. |
| `pagination` | Empty data, first/last and out-of-range pages, offset/cursor semantics, navigation buttons and callback data limits. |
| `integrations` | Documented event payloads and the agreed handling of callback errors. |
| Configuration and Redis lifecycle | Invalid settings, missing extras, ownership and cleanup of connections, key namespaces, Redis failures and atomic updates. |

Test pure logic without services; verify Redis behavior against an isolated real
Redis instance and aiogram behavior through public adapters with a controlled
Telegram transport. Service-dependent scenarios need explicit setup and selection
when introduced. Real bot credentials are not required by the default suite.
Utilities receive focused tests and examples when a concrete helper is implemented.
Compatibility checks will cover the supported Python/aiogram matrix; crash and
concurrency tests will exercise the guarantees actually claimed by each backend.

## Project plans

The [technical specification](TZ.md) and [roadmap](ROADMAP.md) are temporary
planning documents in Russian. Their API examples describe planned behavior.

## License

[MIT](../LICENSE).
