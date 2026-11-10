"""Real producer/Taskiq CLI processes and a loopback Telegram HTTP boundary."""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from importlib.metadata import version
from pathlib import Path
from typing import Any, cast

import pytest
from aiohttp import web
from candidate import prepare_candidate
from redis.asyncio import Redis

ROOT = Path(__file__).resolve().parents[2]


async def until(check: Callable[[], Any]) -> Any:
    async with asyncio.timeout(10):
        while True:
            value = await check()
            if value:
                return value
            await asyncio.sleep(0.02)


@dataclass
class API:
    url: str = ""
    calls: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    failures: list[int] = field(default_factory=list[int])
    hold: asyncio.Event | None = None
    retry_after: int = 1

    async def handle(self, request: web.Request) -> web.Response:
        data = await request.post()
        fields: dict[str, Any] = {}
        for key, value in data.items():
            fields[key] = value.file.read() if isinstance(value, web.FileField) else value
        self.calls.append(
            {"method": request.match_info["method"], "fields": fields, "time": time.monotonic()}
        )
        if self.hold is not None:
            await self.hold.wait()
        status = self.failures.pop(0) if self.failures else 200
        if status == 0:
            assert request.transport is not None
            request.transport.close()
            return web.Response()
        if status != 200:
            return web.json_response(
                {
                    "ok": False,
                    "error_code": status,
                    "description": "synthetic failure",
                    "parameters": {"retry_after": self.retry_after} if status == 429 else {},
                },
                status=status,
            )
        message = {"message_id": len(self.calls), "date": 1, "chat": {"id": 42, "type": "private"}}
        result = [message, message] if request.match_info["method"] == "sendMediaGroup" else message
        return web.json_response({"ok": True, "result": result})


@pytest.fixture
async def api() -> AsyncIterator[API]:
    api = API()
    app = web.Application()
    app.router.add_post("/bot{token}/{method}", api.handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    api.url = str(runner.addresses[0][0])
    api.url = f"http://{api.url}:{runner.addresses[0][1]}"
    try:
        yield api
    finally:
        if api.hold is not None:
            api.hold.set()
        await runner.cleanup()


@dataclass
class Harness:
    client: Redis
    env: dict[str, str]
    prefix: str
    tmp_path: Path
    candidate: bool = False
    processes: list[subprocess.Popen[bytes]] = field(default_factory=list[subprocess.Popen[bytes]])

    async def producer(self, task: str = "deliver", **kwargs: Any) -> None:
        env = self.env | {"SPIKE_TASK": task, "SPIKE_KWARGS": json.dumps(kwargs)}
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "tasks.py",
            cwd=ROOT,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        assert process.returncode == 0, (stdout + stderr).decode()

    async def worker(self, *, idle_ms: int = 5000, max_tasks: int = 4) -> subprocess.Popen[bytes]:
        opened = await self.client.llen(f"{self.prefix}:opened")
        log = self.tmp_path / f"worker-{len(self.processes)}.log"
        with log.open("wb") as output:
            process = await asyncio.to_thread(
                subprocess.Popen,  # Fixed Taskiq command and synthetic environment.
                [
                    sys.executable,
                    "-m",
                    "taskiq",
                    "worker",
                    "tasks:broker",
                    "--workers",
                    "1",
                    "--max-async-tasks",
                    str(max_tasks),
                    "--max-prefetch",
                    "0",
                    "--log-level",
                    "WARNING",
                ],
                cwd=ROOT,
                env=self.env | {"SPIKE_IDLE_MS": str(idle_ms)},
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=sys.platform != "win32",
            )
        self.processes.append(process)

        async def ready() -> bool:
            assert process.poll() is None, log.read_text(errors="replace")
            return await self.client.llen(f"{self.prefix}:opened") > opened

        await until(ready)
        return process

    async def kill(self, process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        if sys.platform == "win32":
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(process.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
        else:
            os.killpg(process.pid, signal.SIGKILL)
        await asyncio.to_thread(process.wait, 5)

    async def values(self, key: str) -> list[str]:
        # This observer client is constructed with decode_responses=True.
        return cast("list[str]", await self.client.lrange(f"{self.prefix}:{key}", 0, -1))

    async def completed(self, job: str) -> None:
        async def found() -> bool:
            return job in await self.values("completed")

        await until(found)

    async def stage(self, job: str, phase: str) -> None:
        async def found() -> bool:
            return f"{job}:{phase}" in await self.values("stages")

        await until(found)

    async def release(self, job: str, phase: str) -> None:
        await self.client.rpush(f"{self.prefix}:release:{job}:{phase}", "go")

    async def pending(self) -> int:
        summary = await self.client.xpending(f"{self.prefix}:queue", f"{self.prefix}:group")
        return int(summary["pending"])

    async def pending_ids(self) -> set[str]:
        entries = await self.client.xpending_range(
            f"{self.prefix}:queue",
            f"{self.prefix}:group",
            "-",
            "+",
            1000,
        )
        return {str(entry["message_id"]) for entry in entries}


@pytest.fixture
async def harness(
    redis_url: str,
    api: API,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> AsyncIterator[Harness]:
    prefix = f"spike:{uuid.uuid4().hex}"
    env = os.environ | {
        "SPIKE_REDIS_URL": redis_url,
        "SPIKE_PREFIX": prefix,
        "SPIKE_API_URL": api.url,
    }
    candidate = bool(request.config.getoption("--recovery-candidate")) or bool(
        request.config.getoption("--durable")
    )
    if request.config.getoption("--durable"):
        env["SPIKE_DURABLE"] = "1"
    if request.config.getoption("--transport-wheel"):
        assert version("taskiq-redis") == "1.2.4+aiogramext.2"
        candidate = True
        env["SPIKE_BROKER_SHA256"] = (
            "0569fb4baf7b4a0376eeb96eb50e355530e0ca4c7453c4567dbb8da102e3bef3"
        )
    elif candidate:
        overlay = tmp_path / "transport"
        broker_file = prepare_candidate(overlay)
        env["PYTHONPATH"] = str(overlay) + os.pathsep + env.get("PYTHONPATH", "")
        env["SPIKE_BROKER_SOURCE"] = str(broker_file)
    async with Redis.from_url(redis_url, decode_responses=True) as client:  # pyright: ignore[reportUnknownMemberType]
        harness = Harness(client, env, prefix, tmp_path, candidate=candidate)
        try:
            yield harness
        finally:
            for process in harness.processes:
                await harness.kill(process)
