"""Experimental durable delivery state at the Redis boundary."""

import json
import re
import uuid
from pathlib import Path
from typing import Any, cast

from payload import decode
from redis.asyncio import Redis

SCRIPT = Path(__file__).with_suffix(".lua").read_text(encoding="utf-8")
MAX_PAYLOAD_BYTES = 262144
LEASE_MS = 3000
MAX_ATTEMPTS = 3
MAX_RETENTION_MS = 31536000000


class DeliveryJournal:
    def __init__(self, client: Redis, prefix: str, *, retention_ms: int = 86400000) -> None:
        if type(retention_ms) is not int or not 0 < retention_ms <= MAX_RETENTION_MS:
            raise ValueError(retention_ms)

        self.client = client
        self.prefix = prefix
        self.retention_ms = retention_ms

    async def enqueue(self, job: str, wire: dict[str, Any]) -> tuple[bool, str]:
        decode(wire)
        serialized = json.dumps(wire, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(serialized.encode()) > MAX_PAYLOAD_BYTES:
            raise ValueError(MAX_PAYLOAD_BYTES)

        result = await self._eval("enqueue", job, uuid.uuid4().hex, wire=serialized)
        return bool(result[0]), str(result[1])

    async def snapshot(self, job: str) -> dict[str, str]:
        return cast("dict[str, str]", await self.client.hgetall(self.key(job)))

    async def claim(self, job: str, generation: str, owner: str) -> str:
        return str(await self._eval("claim", job, generation, owner=owner, lease_ms=LEASE_MS))

    async def release(self, job: str, generation: str, owner: str) -> bool:
        return bool(await self._eval("release", job, generation, owner=owner))

    async def begin_attempt(self, job: str, generation: str, owner: str) -> int:
        return int(await self._eval("attempt", job, generation, owner=owner, maximum=MAX_ATTEMPTS))

    async def fail(
        self,
        job: str,
        generation: str,
        owner: str,
        error: str,
        *,
        retry_ms: int | None,
    ) -> None:
        await self._eval(
            "fail",
            job,
            generation,
            owner=owner,
            error=error,
            retry_ms=retry_ms,
            maximum=MAX_ATTEMPTS,
        )

    async def deadletter(self, job: str, generation: str, owner: str) -> str:
        return str(
            await self._eval(
                "dead",
                job,
                generation,
                owner=owner,
                retention_ms=self.retention_ms,
            )
        )

    async def replay(self, job: str, entry: str) -> tuple[bool, str]:
        result = await self._eval("replay", job, uuid.uuid4().hex, entry=entry)
        return bool(result[0]), str(result[1])

    async def renew(self, job: str, generation: str, owner: str) -> None:
        await self._eval("renew", job, generation, owner=owner, lease_ms=LEASE_MS)

    async def succeed(self, job: str, generation: str, owner: str) -> None:
        await self._eval("succeed", job, generation, owner=owner, retention_ms=self.retention_ms)

    async def outbox(self) -> list[tuple[str, str]]:
        entries = cast("list[str]", await self.client.zrange(f"{self.prefix}:outbox", 0, 99))  # pyright: ignore[reportUnknownMemberType]
        return [(entry.rsplit(":", 1)[0], entry.rsplit(":", 1)[1]) for entry in entries]

    async def dispatched(self, job: str, generation: str) -> None:
        await self.client.zrem(f"{self.prefix}:outbox", f"{job}:{generation}")

    async def cleanup(self) -> int:
        return int(await self._eval("cleanup", "cleanup", ""))

    def key(self, job: str) -> str:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", job):
            raise ValueError(job)

        return f"{self.prefix}:job:{job}"

    async def _eval(self, operation: str, job: str, generation: str, **values: Any) -> Any:
        return await self.client.eval(
            SCRIPT,
            5,
            self.key(job),
            f"{self.prefix}:lease:{job}:{generation}",
            f"{self.prefix}:outbox",
            f"{self.prefix}:durable-dlq",
            f"{self.prefix}:dead-expiry",
            json.dumps({"op": operation, "job": job, "generation": generation, **values}),
        )
