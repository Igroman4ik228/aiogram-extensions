"""Control only a test-owned disposable Redis container."""

import subprocess
import time
from dataclasses import dataclass

from redis import Redis
from redis.backoff import NoBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from redis.retry import Retry


@dataclass(frozen=True)
class RedisService:
    docker: str
    name: str
    url: str

    def run(self, *args: str) -> str:
        return subprocess.check_output(  # noqa: S603 -- Test-owned Docker commands only.
            [self.docker, *args],
            text=True,
            stderr=subprocess.STDOUT,
            timeout=30,
        ).strip()

    def wait(self) -> None:
        with Redis.from_url(  # pyright: ignore[reportUnknownMemberType]
            self.url,
            socket_connect_timeout=0.2,
            socket_timeout=0.2,
            retry=Retry(NoBackoff(), 0),
        ) as client:
            deadline = time.monotonic() + 10
            while True:
                try:
                    if client.ping():  # pyright: ignore[reportUnknownMemberType]
                        return
                except (RedisConnectionError, RedisTimeoutError):
                    if time.monotonic() >= deadline:
                        raise

                time.sleep(0.05)

    def crash_restart(self) -> None:
        self.run("kill", "--signal", "KILL", self.name)
        self.run("start", self.name)
        self.wait()

    def pause(self) -> None:
        self.run("pause", self.name)

    def unpause(self) -> None:
        self.run("unpause", self.name)
        self.wait()
