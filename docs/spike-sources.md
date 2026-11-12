# Redis queue spike: source findings

Reviewed on 10 November 2026. These are source observations and experiment
requirements, not proof of delivery guarantees. Runtime results are in the
[spike report](redis-queue-spike.md). The versions below are used in the stage 1 experiment, not
additional supported package dependencies.

## Versions

| Component | Pinned candidate | Published compatibility evidence |
| --- | --- | --- |
| Taskiq | 0.13.0 | Python `>=3.10,<4`; classifiers include 3.11–3.14; development status remains Alpha. [Release metadata](https://pypi.org/project/taskiq/0.13.0/). |
| taskiq-redis | 1.2.4 | Python `>=3.10,<4`, classifiers include 3.11–3.14; declares `redis>=8.0.0,<9` and `taskiq>=0.13.0`. [Tagged metadata](https://github.com/taskiq-python/taskiq-redis/blob/1.2.4/pyproject.toml). |
| aiogram | 3.31.0 | Existing project lock; use the matching source tag for API inspection. |
| Redis server | Select and record an exact image version/digest | `XAUTOCLAIM` exists since Redis 6.2; this is a command prerequisite, not a tested server compatibility range. [Redis command reference](https://redis.io/docs/latest/commands/xautoclaim/). |

taskiq-redis 1.2.4 release notes explicitly add Python 3.14 support and Taskiq
0.13.0 compatibility. Version 1.2.3 previously changed Redis dependencies and
addressed a StreamBroker race. Declared support still needs executable checks on
the project matrix. [1.2.4 notes](https://github.com/taskiq-python/taskiq-redis/releases/tag/1.2.4),
[1.2.3 notes](https://github.com/taskiq-python/taskiq-redis/releases/tag/1.2.3).

## Transport and recovery

### Reference implementation: django-aiogram

[django-aiogram](https://github.com/CorneiZeR/django-aiogram) is a reference for
Redis Streams recovery, acknowledgement timing and operator replay. Review these
paths when changing the corresponding behavior here.

- Study how consumers reclaim pending messages after a worker dies, including
  when no new messages arrive. Preserve pending entries when designing retention.
  [Redis Streams](https://corneizer.github.io/django-aiogram/latest/Redis-Streams/)
- Verify that scheduling an async send leaves the delivery pending. Versions
  before 3.1.0 acknowledged when the handler returned, which could lose an
  in-flight send on shutdown. Persist the terminal outcome before acknowledging.
  [Delivery history](https://github.com/CorneiZeR/django-aiogram/blob/97bbedbb722437ec4e497d1797c89b102d4fe7cd/docs/wiki/Delivery.md#L289)
- Use the replay claim as a reference for concurrent operator requests. Also test
  the crash window where publication succeeded and its reply was lost. Taking
  over an expired claim in that window can publish a duplicate.
  [Replay implementation](https://github.com/CorneiZeR/django-aiogram/blob/97bbedbb722437ec4e497d1797c89b102d4fe7cd/src/django_aiogram/management/commands/tgbot_replay.py#L177-L202)

### Taskiq Redis candidate

The tagged `RedisStreamBroker` source shows:

- Enqueue uses `XADD`; acknowledgement uses `XACK` and does not delete the stream
  entry. Optional `maxlen` trims entries independently of task completion.
- `startup()` declares a group; `consumer_id="$"` is its default starting ID.
  Always start the producer broker before enqueue and test enqueue before workers.
- `idle_timeout` defaults to 600000 milliseconds; the reclaim lock timeout defaults
  to `None`. Set and test an explicit finite lock timeout.
- `listen()` skips the reclaim block when `XREADGROUP` returns no new entries.
  **Inference:** restarting an idle worker alone may not recover pending messages;
  compare idle recovery with recovery after enqueueing a trigger task.
- `XAUTOCLAIM` uses the configured batch size; its returned cursor is not carried
  into subsequent calls. Test recovery beyond one batch.
- The broker constructs its own `BlockingConnectionPool` and disconnects it on
  shutdown. Its constructor provides no shared-client/pool argument.

These observations come from [tag 1.2.4, not an assumed latest implementation](https://github.com/taskiq-python/taskiq-redis/blob/1.2.4/taskiq_redis/redis_broker.py).
The [main branch](https://github.com/taskiq-python/taskiq-redis/blob/main/taskiq_redis/redis_broker.py)
was inspected separately; its availability does not establish what an installed
wheel contains. Compare the installed artifact before attributing a runtime result
to either source.

Relevant issue reports are evidence of test cases, not confirmation that the
pinned wheel reproduces them:

| Issue | State when checked | Implication for experiments |
| --- | --- | --- |
| [#131](https://github.com/taskiq-python/taskiq-redis/issues/131) | Open | Reports an orphaned reclaim lock after hard kill on older versions. The pinned constructor has a timeout option, but its default remains unlimited. Kill during reclaim and inspect the lock TTL. |
| [#101](https://github.com/taskiq-python/taskiq-redis/issues/101) | Open | Reports concurrent redelivery of a live long-running task after `idle_timeout`. Check multiple consumers with task duration both below and above the threshold. |
| [#123](https://github.com/taskiq-python/taskiq-redis/issues/123) | Closed via #126 | Reports simultaneous reclaim on older versions. A closed issue and release note do not replace a multi-worker regression experiment. |

## Acknowledgements, retries and DLQ

Taskiq exposes `when_received`, `when_executed`, `when_saved` and `manual` ack
modes. The public `Context.ack()` supports explicit task-owned acknowledgement;
`Context.requeue()` republishes the message and suppresses storing its result.
Republish and acknowledgement are separate operations. [Tagged acknowledgement
types](https://github.com/taskiq-python/taskiq/blob/0.13.0/taskiq/acks.py),
[tagged Context](https://github.com/taskiq-python/taskiq/blob/0.13.0/taskiq/context.py).

The default receiver mode is `when_saved`. Task exceptions are converted to
results and sent through error middleware; failed tasks can still be acknowledged.
Malformed messages and unknown task names are also acknowledged. A result-backend
save exception is logged, and the normal callback path can continue to
acknowledgement. Recovery of these cases must not be assumed. Public middleware
hooks include `on_error`, `post_execute` and `post_save`.
[Tagged receiver](https://github.com/taskiq-python/taskiq/blob/0.13.0/taskiq/receiver/receiver.py),
[middleware interface](https://github.com/taskiq-python/taskiq/blob/0.13.0/taskiq/abc/middleware.py).

`SimpleRetryMiddleware` republishes immediately, preserves task ID, and uses
`retries < max_retries`: a value of 3 permits three total executions. It has
exception-type filtering but no Telegram `retry_after` handling and no terminal
DLQ write. [Tagged simple retry implementation](https://github.com/taskiq-python/taskiq/blob/0.13.0/taskiq/middlewares/simple_retry_middleware.py).

`SmartRetryMiddleware` either schedules through a supplied schedule source or
republishes with a `delay` label. **Inference:** the latter alone cannot implement
a delay with RedisStreamBroker, whose enqueue code does not consume that label.
Do not combine independent retry owners. Prototype one bounded retry/DLQ policy,
and test failed persistence and crash windows before claiming reliable handoff.
[Tagged smart retry implementation](https://github.com/taskiq-python/taskiq/blob/0.13.0/taskiq/middlewares/smart_retry_middleware.py).

## Worker lifecycle and serialization

Worker/client startup and shutdown events are public broker extension points.
Create the Bot in worker startup, store it in worker state, and close its session
in worker shutdown; a hard kill cannot run shutdown callbacks. The CLI accepts
`module:broker` or a broker factory, `--workers`, `--max-async-tasks`,
`--max-prefetch`, and `--ack-type`.
[Tagged broker lifecycle](https://github.com/taskiq-python/taskiq/blob/0.13.0/taskiq/abc/broker.py),
[CLI arguments](https://github.com/taskiq-python/taskiq/blob/0.13.0/taskiq/cli/worker/args.py),
[worker startup](https://github.com/taskiq-python/taskiq/blob/0.13.0/taskiq/cli/worker/run.py).

Taskiq prepares top-level Pydantic arguments using `model_dump(mode="json")` and
dataclasses using `asdict`; its default formatter then serializes a JSON-mode
message. Setting a task ID does not add an enqueue uniqueness check.
[Tagged kicker](https://github.com/taskiq-python/taskiq/blob/0.13.0/taskiq/kicker.py),
[formatter](https://github.com/taskiq-python/taskiq/blob/0.13.0/taskiq/formatters/proxy_formatter.py).

aiogram's `deserialize_telegram_object()` returns JSON-compatible `.data` plus a
separate `.files` mapping containing live `InputFile` instances. The `_to_python`
variant discards the mapping. This is not a Redis-safe local-file representation.
Use explicit JSON file descriptors and reconstruct local uploads in the worker;
file IDs and URLs remain strings. Resolve bot defaults deliberately.
[Tagged aiogram serialization helper](https://github.com/aiogram/aiogram/blob/v3.31.0/aiogram/utils/serialization.py).

`FSInputFile.read()` opens the path in the process performing the upload, whereas
`BufferedInputFile` keeps bytes in memory and `URLInputFile` can retain a Bot.
Therefore shared local paths need an explicit contract, and arbitrary runtime
objects must be rejected before enqueue. Test all required send methods and nested
media rather than extrapolating from `send_message`.
[Tagged input files](https://github.com/aiogram/aiogram/blob/v3.31.0/aiogram/types/input_file.py).

For a controlled Telegram boundary, aiogram permits a custom `BaseSession`;
`check_response()` maps a response `retry_after` into `TelegramRetryAfter`, whose
`retry_after` attribute is seconds. A local HTTP API additionally exercises request
construction and multipart uploads.
[Tagged BaseSession](https://github.com/aiogram/aiogram/blob/v3.31.0/aiogram/client/session/base.py),
[tagged exceptions](https://github.com/aiogram/aiogram/blob/v3.31.0/aiogram/exceptions.py).

Explicitly close owned Redis resources. A separately supplied redis-py pool has
its own ownership; broker shutdown must not be described as closing an unrelated
application client. Current redis-py documentation also notes changed RESP defaults
in version 8; record the tested client/protocol combination.
[Official asyncio resource guidance](https://redis.readthedocs.io/en/stable/examples/asyncio_examples.html).
