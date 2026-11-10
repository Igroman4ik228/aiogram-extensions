# Aiogram Extensions

[Русский](README_RU.md)

Modular extensions and utilities for aiogram 3. Planned features include rate
limiting, message queues, pagination and observability hooks.

**Under development:** only the package foundation is available. The planned
features are not implemented yet.

## Getting started

Requires Python 3.11–3.14 and [uv](https://docs.astral.sh/uv/). The supported aiogram
range is `>=3.29.1,<3.32`. Redis and Taskiq are not included.

From the repository root, install the package and development dependencies:

```console
uv sync --locked
```

## Examples

Run these standalone scripts from the repository root using the locked project
environment:

```console
uv run --locked examples/package_info.py
uv run --locked examples/bot_lifecycle.py
```

- [Package information](../examples/package_info.py): import the package and inspect
  its license and typing marker.
- [Session lifecycle](../examples/bot_lifecycle.py): open and close an aiogram HTTP
  session with a synthetic token, without sending Telegram API requests. This
  demonstrates the aiogram dependency, not an extension API.

Both examples run without credentials, Telegram access or Redis. Extension examples
will be added alongside their implementations; planned APIs are not runnable yet.
Examples are linted, type-checked and [tested as subprocesses](../tests/integration/test_examples.py).

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

The main package suite contains integration tests. Create unit and E2E directories
with their first tests, rather than empty placeholders. Current layout:

```text
tests/
├── conftest.py
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

The following checks will accompany implementations of the modules specified in
the [technical specification](TZ.md); these modules are not implemented yet.

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
