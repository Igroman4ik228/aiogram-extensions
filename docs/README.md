# Aiogram Extensions

[Русский](README_RU.md)

Modular extensions and utilities for aiogram 3.

**Under development.** Rate limiting, pagination, typed callbacks and Redis
connection management are available. Message queues are planned.

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
uv run --locked examples/pagination_core.py
uv run --locked examples/pagination_bot.py
uv run --locked examples/rate_limit.py
```

- [Package information](../examples/package_info.py): import the package and inspect
  its license and typing marker.
- [Session lifecycle](../examples/bot_lifecycle.py): open and close an aiogram HTTP
  session with a synthetic token, without sending Telegram API requests. This
  demonstrates the aiogram dependency, not an extension API.

- [Contracts and callbacks](../examples/contracts.py): configurations and an
  explicitly emitted event, without running a limiter or queue.
- [Redis in a handler](../examples/redis_lifecycle.py): feed a local `/redis` update,
  get the Redis client through aiogram DI and build a namespaced key. Requires redis-py.

- [Pagination core](../examples/pagination_core.py): ordered collection pages,
  an asynchronous cursor adapter and bounded plain-text previews.
- [Pagination bot](../examples/pagination_bot.py): offline page previews and
  a ready-to-register `/catalog` router with buttons and formatted titles. For
  live polling, set `BOT_TOKEN` and run `uv run --locked examples/pagination_bot.py --live`.
  Live mode requires a real bot token and access to Telegram.

- [Rate limiting](../examples/rate_limit.py) demonstrates direct checks and
  independent budgets for users in one chat.

All examples in their default modes run without credentials, network access or external services.
Examples are linted, type-checked and [tested as subprocesses](../tests/integration/test_examples.py).

## Pagination and utilities

Core imports (`aiogram_extensions.pagination` or `.pagination.core`) do not load
aiogram or Redis. Import Telegram UI separately from `.pagination.aiogram`.

### Text lists in aiogram

```python
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from aiogram_extensions.pagination import CursorPage, Page, Paginator
from aiogram_extensions.pagination.aiogram import NavigationConfig, TextPagination

router = Router()


def render(page: Page[str] | CursorPage[str]) -> str:
    return "\n".join(page.items) or "The catalog is empty."


catalog = TextPagination(
    Paginator(["Apple", "Banana", "Cherry"], page_size=2),
    render=render,
    config=NavigationConfig(prefix="catalog", current_text="{page}/{pages}"),
)
router.include_router(catalog.router)


@router.message(Command("catalog"))
async def show_catalog(message: Message) -> None:
    if message.from_user is not None:
        await catalog.answer(message)
```

Include `router` in the dispatcher. `answer(message)` returns the sent `Message`
with the first page and buttons. The included router answers button presses,
updates the original message, and clears callback references on shutdown. Only the
user who opened the list can turn its pages. Use a distinct callback prefix for
each list. If a parent router has a catch-all callback handler, filter it so these
callbacks can reach the pagination router.

`TextPagination` accepts either paginator. The renderer returns a plain string or
aiogram's `Text` for formatted content, as in the [catalog bot](../examples/pagination_bot.py).
References live in the current process and follow the TTL and capacity below.
Use `Navigation` directly for custom UI, such as photo captions or inline-mode messages.

### Ordered sources and typed pages

`Paginator(source, *, page_size=10)` accepts a Python `Sequence` or an async
`OffsetSource`. Collections use `SequenceSource`: a shallow tuple snapshot in the
original order; later collection insertions/removals do not change it. Item objects
are not copied. Text and bytes are rejected as collections.

`await paginator.page(number=1)` uses one-based numbering and offset
`(number - 1) * page_size`. Frozen `Page` fields are tuple `items`, `number`, `limit`
and `total`; properties are `offset`, `total_pages`, `previous_page`, `next_page`.
An empty source has page 1 of 1 with no navigation. Positive integer sizes and page
numbers reject booleans/fractions; pages beyond the end raise `PageOutOfRangeError`.

Implement `async fetch(*, offset: int, limit: int) -> OffsetResult[T]` for a custom
offset source. Return `OffsetResult(items=tuple(...), total=...)` with a non-negative
integer total and exactly the requested slice, including the shorter last page.
`SequenceSource.fetch` also supports direct arbitrary non-negative offsets and
positive limits. Each paginator request reads the source again. The adapter must
provide deterministic ordering (including a unique tie-breaker), consistent rows
and count within one read, and non-blocking I/O. Inserts/deletes between reads can
shift offset pages; no cross-request database snapshot is implied. Inconsistent
source results raise `PaginationError`; input validation raises `ValueError`.

`CursorPaginator(source, *, page_size=10)` accepts a `CursorSource` implementing
`async fetch(*, cursor: str | None, limit: int) -> CursorResult[T]`. `page(cursor=None)`
starts traversal. Return tuple `items` (at most `limit`), `next_cursor=None` and
`previous_cursor=None` for absent directions, or non-empty opaque strings for
existing ones. To return to the first page, supply an explicit start token: `None`
means no previous/next link. Frozen `CursorPage` adds the requested `cursor` and
`limit`; it has no inferred page number or total. Tokens pointing to the current
non-None cursor are rejected. The adapter owns keyset ordering, token validation,
versioning and consistency under mutations; reject unusable tokens with
`InvalidCursorError`. Other source errors and cancellation propagate unchanged.
See the [executable source adapter](../examples/pagination_core.py).

### Aiogram navigation and callback lifecycle

`Navigation(paginator, *, config=None, clock=time.monotonic)` supports either paginator.
`keyboard(page, *, scope)` returns `InlineKeyboardMarkup`, omitting unavailable
directions. It replaces references for the same scope. Use a scope bound to the
message and, for private controls, the authorized user; recompute it from the query
before resolution.

`NavigationConfig` is a frozen keyword-only dataclass: `prefix="aep1"` (1–32 ASCII
letters/digits/`_.-`), `previous_text="←"`, `next_text="→"`, `current_text="{page}"`,
`layout=(("previous", "current", "next"),)`, `ttl=300` seconds, `max_entries=1000`
(at least 3 references), `stale_text="Navigation expired. Open the list again."`,
`invalid_text="Invalid navigation."`. Labels must be non-empty. Layout contains
each action exactly once across non-empty rows. The current label supports Python
format fields `page`, `pages`, `total`, `count`; cursor pages use `"•"`, `"?"`, `"?"`
for the first three. Use string-compatible formats with cursor pagination.

Buttons carry only a prefix and a random 24-character ASCII reference (at most
57 UTF-8 bytes), within [Telegram's 1–64 byte constraint](https://core.telegram.org/bots/api#inlinekeyboardbutton).
Cursors stay in memory, including long or Unicode tokens. References expire at
the TTL boundary; expired entries are removed on access, oldest references are
evicted at capacity. `clear(scope=...)` invalidates one scope, `clear()` all scopes.
State is local to the `Navigation` instance and lost on restart. Route callbacks
to the same instance; shared storage across processes is not provided. Use a
distinct prefix for each `Navigation` instance registered in the same router tree.

`await navigation.resolve(data, *, scope)` returns a page, or `None` for the current
indicator. Malformed data raises `InvalidNavigationError`; missing, expired,
superseded, wrong-scope references and removed pages/rejected cursors raise
`StaleNavigationError`. References are checked again after reading the source.
Both errors inherit `PaginationError`/`ValueError`. It performs no
Telegram requests. `matches(query)` is a router filter for the configured prefix.
`await handle(query, *, scope, render)` returns `False` for other prefixes; owned
queries are answered and return `True`. Invalid/stale queries receive configured
text; the current indicator is only acknowledged. Valid navigation calls async
`render(query, page, markup)` after acknowledgement; the application edits the
message and handles accessible/inline message differences. Source, Telegram and
render errors and cancellation propagate. Serialize concurrent updates per scope
in the application. If editing fails, rebuild the keyboard; invalidated references
are not rolled back. No background cleanup task or connections are started.

### Plain-text previews

`aiogram_extensions.utilities.truncate_text(text, *, max_units, suffix="…")` fits
plain text and a truncation marker in a UTF-16 code-unit budget. Unlike Python
slicing it accounts for astral characters as two units and preserves whole code
points. The pagination examples use it to bound catalog row previews. The suffix
must fit even when the text needs no truncation; invalid budgets raise `ValueError`.
Input must be valid Unicode. It does not preserve grapheme clusters, HTML/Markdown
or existing entities: truncate before escaping/constructing entities. This is a
conservative text budget, not automatic enforcement of every Telegram method's
limits. For rich formatting use aiogram's own formatting tools; this helper does
not replace them. See [Telegram entity offsets](https://core.telegram.org/bots/api#messageentity).

## Stage 2 contracts

Import the public API from the feature modules. The queue behavior below
describes the contract for stages 5–6.

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

Create a `RateLimiter(config, direction="incoming")` for direct checks. Pass
`RateLimitContext(bot_id=..., user_id=..., chat_id=...)` to
`await limiter.check(context)` for a single check. Use
`await limiter.acquire(context)` to apply the configured reject or wait policy.
The configuration and direction are read-only. Create a new limiter to change them.

```python
from aiogram_extensions.rate_limit import RateLimitConfig, RateLimiter, RateRule
from aiogram_extensions.rate_limit.middleware import (
    IncomingRateLimitMiddleware,
    OutgoingRateLimitMiddleware,
)

incoming = RateLimiter(
    RateLimitConfig(rules=(RateRule(name="activity", limit=5, window=1),)),
    direction="incoming",
)
outgoing = RateLimiter(
    RateLimitConfig(
        rules=(RateRule(name="send", scope="chat", limit=1, window=1),),
        on_limit="wait",
    ),
    direction="outgoing",
)
dispatcher.update.outer_middleware(IncomingRateLimitMiddleware(incoming))
bot.session.middleware(OutgoingRateLimitMiddleware(outgoing))
```

Use these example budgets as starting settings for your workload. Telegram quotas
can differ. Incoming middleware accepts `allowed_users`, `allowed_chats` and an
`exempt(event)` predicate. A rejection ends update processing. Outgoing rejection
raises `RateLimitExceededError` with a `result` attribute. Outgoing chat rules
apply to methods with a `chat_id`. Group and custom rules require a
`chat_type(bot, chat_id)` or `custom_key(bot, method)` resolver, respectively.
A resolver can return `None` when the rule does not apply. Use consistent chat
identifiers and keep tokens and message contents out of custom keys.
[aiogram session middleware](https://docs.aiogram.dev/en/latest/api/session/middleware.html)

Keys isolate direction, rule name, algorithm, scope and numeric bot ID.
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
monotonic clock and performs bounded cleanup on activity. Redis uses the server's
clock and key TTLs at millisecond resolution, rounding durations up.

Memory is the default. Reuse one `MemoryRateLimitBackend()` on one event loop to
share budgets between limiters. For multiple processes, use
`backend=RedisRateLimitBackend(manager)` with a started `RedisConnectionManager`
in each process and the same Redis database and prefix. Use standalone Redis
7.2.5 (the tested version) with `noeviction` and permission to run Lua scripts and
their commands. With a supplied client, disable automatic retries of commands
whose outcome can be unknown after a timeout.
[Redis Lua scripting](https://redis.io/docs/latest/develop/programmability/eval-intro/)

Check/charge all applicable rules atomically. On rejection, charge none.
The Redis backend makes one request per decision, running the check and charge
inside one Lua script. `triggered_rules` follows configuration order;
`retry_after` is the maximum wait
among blocking rules. HTTP failure does not refund an admitted attempt. Reject
returns denial; wait asynchronously sleeps and rechecks, without reserving capacity
while asleep. Cancellation propagates; already admitted attempts remain counted.
Backend failure raises `RateLimitBackendError` before the request proceeds.
After `TelegramRetryAfter`, outgoing middleware records a bot-wide cooldown
because Telegram supplies a duration without a quota scope. It then propagates
the original error so the caller can decide whether to retry. A cooldown write
failure raises `RateLimitBackendError`. During cooldown, denials include
`telegram_retry_after` after any blocking rule names. Pass `events=EventDispatcher(...)`
to receive `LimitExceededEvent` and `BackendErrorEvent`.

To include tests with a real Redis server, set `AIEXT_TEST_REDIS_URL` to an isolated
instance and run `uv run --locked pytest tests/integration/rate_limit`. The Redis
account used by the tests needs permission to create and remove a temporary ACL
user. The Redis server cases skip when the variable is absent. Middleware tests
use a Telegram stub.

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

Wrap polling in `redis_context` to make a Redis client available to aiogram handlers
for the duration of the application.

```python
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message
from redis.asyncio import Redis

from aiogram_extensions.redis import RedisConfig, redis_context

dispatcher = Dispatcher()


@dispatcher.message(Command("redis"))
async def redis_status(message: Message, redis: Redis) -> None:
    await redis.ping()
    await message.answer("Redis is reachable.")


async def run(bot: Bot, redis_url: str) -> None:
    async with redis_context(dispatcher, RedisConfig(url=redis_url)):
        await dispatcher.start_polling(bot, handle_as_tasks=False)
```

Install the `redis` extra and provide a reachable Redis server to run this command.
`redis_context` puts the client into dispatcher data under `redis` and removes it on
exit, including after an error. Use `key="cache"` and a handler parameter named
`cache` for a different name. The key must be free in dispatcher data. Pass
`client=...` to borrow an existing connection. Use `as manager` to build namespaced
keys via `manager.key(...)`. The [offline example](../examples/redis_lifecycle.py)
demonstrates injection with a local update. [Aiogram DI documentation](https://docs.aiogram.dev/en/3.31.0/dispatcher/dependency_injection.html)

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
│   ├── pagination/test_core.py
│   ├── rate_limit/test_contracts.py
│   ├── utilities/test_text.py
│   └── test_redis.py
└── integration/
    ├── pagination/
    │   ├── test_bot_example.py
    │   └── test_navigation.py
    ├── test_examples.py
    ├── test_imports.py
    ├── test_redis_context.py
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
