# Redis spike: durability assumptions

Source review on 10 November 2026 of `journal.lua`, `journal.py`, `durable.py`,
`tasks.py`, `build_transport.py` and `recovery.patch`. This note supplies protocol
constraints; it contains no new runtime measurements. Test outcomes belong in
the [experiment report](redis-queue-spike.md).

## Redis transitions

EVAL prevents interleaving while a script runs; it does **not** provide general
rollback after a runtime error. Redis's own 7.4.2 test increments a key, encounters
an invalid command, then observes the retained increment. Therefore key-type
preflight protects only one failure class. Validate every argument that can fail
after an earlier write, and grant all required command/key permissions.
[Atomic execution](https://redis.io/docs/latest/develop/programmability/eval-intro/),
[upstream regression test](https://github.com/redis/redis/blob/7.4.2/tests/unit/scripting.tcl#L1190).

The review identified positive-only retention validation and incomplete ACLs as
partial-write risks. The implementation now rejects non-integers and durations
outside 1 ms–365 days before I/O and checks the command/key ACL set before writing.
The parent experiment reproduced invalid inputs and a stranded enqueue, then
verified the fixes; see the report for runtime evidence. Redis 7 provides
`redis.acl_check_cmd()` for checking an operation's permissions before writing.
This note itself remains a source review.
[Lua command/error/ACL API](https://redis.io/docs/latest/develop/programmability/lua-api/).

`appendonly yes` with `appendfsync always` fsyncs the AOF before replies, subject
to the OS/storage honoring persistence. `no-appendfsync-on-rewrite` must remain
`no`; enabling it weakens fsync during rewrites. `noeviction` preserves keys under
memory pressure by rejecting allocating writes; it does not provide unlimited
capacity. A restart using the same data directory is different from losing the
volume. Asynchronous replication/failover can lose acknowledged writes and is
outside a single-instance crash experiment.
[Persistence](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/),
[7.4.2 configuration](https://github.com/redis/redis/blob/7.4.2/redis.conf#L1357),
[eviction policy](https://redis.io/docs/latest/develop/reference/eviction/),
[replication limits](https://redis.io/docs/latest/operate/oss_and_stack/management/replication/).

## Ownership and external effects

A unique random owner token, SET NX PX, conditional renewal and owner-checked
release match Redis's single-instance lease pattern. They fence journal changes
from an old owner. Redis explicitly warns that a living process can lose its
lease and recommends fencing at the protected resource. Renewal depends on timely
scheduling, connectivity and clock behavior.
[Official lease guidance](https://redis.io/docs/latest/develop/clients/patterns/distributed-locks/).

Telegram sendMessage exposes no lease-token or idempotency-key parameter.
**Inference:** the journal cannot fence an external Telegram effect. A process
pause beyond lease expiry, an in-flight request when ownership changes, or loss
of the response can still produce duplicates. Cancelling the local request does
not establish whether Telegram already applied it. HTTP's retry guidance
distinguishes this uncertainty for non-idempotent operations. Finite attempts and
DLQ bound policy; they cannot guarantee a successful Telegram send exactly once.
[Telegram method contract](https://core.telegram.org/bots/api#sendmessage),
[HTTP retry semantics](https://www.rfc-editor.org/rfc/rfc9110.html#section-9.2.2).

## Outbox and reclaim

Publishing through public `kiq()` and removing the durable outbox entry only after
success deliberately admits duplicate publication after a crash or unknown reply.
The journal's generation/status checks must absorb those duplicates. Taskiq's
custom task ID alone performs no uniqueness check. Manual `Context.ack()` permits
persisting success or DLQ before acknowledgement; those remain separate operations.
[Taskiq 0.13.0 kicker](https://github.com/taskiq-python/taskiq/blob/0.13.0/taskiq/kicker.py),
[Context](https://github.com/taskiq-python/taskiq/blob/0.13.0/taskiq/context.py).

XAUTOCLAIM returns a cursor even when a scan claims no entries. Continue from that
cursor and restart at `0-0` after a complete scan; previously ineligible entries
can later become eligible. The patch's per-stream cursor supports this protocol.
Finite XREADGROUP blocking is still required for periodic idle scanning. No
pending-entry safety follows from trimming payloads: Redis can remove deleted
entries from the pending list during reclaim.
[XAUTOCLAIM contract](https://redis.io/docs/latest/commands/xautoclaim/).

## Artifact and decision boundary

The local version `1.2.4+aiogramext.2` identifies a downstream compatible patch,
not an official upstream release. Pin the exact local artifact and hash:
`==1.2.4` alone also matches local variants. Rebuilding requires updated METADATA,
WHEEL and every RECORD digest; RECORD itself has no digest. Embedded provenance
and an external hash make changes auditable but do not establish an upstream
signature or publisher attestation.
[Local versions](https://packaging.python.org/en/latest/specifications/version-specifiers/#local-version-identifiers),
[wheel integrity format](https://packaging.python.org/en/latest/specifications/binary-distribution-format/).

A completed, passing spike can support a **conditional stack decision** for the
exact patched artifact, tested versions, single Redis instance, persistent volume,
complete ACLs, bounded configuration and documented duplicate policy. It does not
establish a production queue API, arbitrary failover/partitions, or completion of
the MVP. The concrete validation/ACL findings have been addressed; attach the final
test evidence before making that decision.
