"""Optional Redis client ownership and isolated key namespaces."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Self
from urllib.parse import quote, urlsplit

from aiogram_extensions._validation import (
    ConfigurationError,
    identifier,
    nonempty_string,
    positive_integer,
    require_instance,
    seconds,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from types import TracebackType

    from aiogram import Dispatcher
    from redis.asyncio import Redis

__all__ = [
    "MissingRedisExtraError",
    "RedisConfig",
    "RedisConnectionManager",
    "RedisLifecycleError",
    "redis_context",
]


@dataclass(frozen=True, kw_only=True)
class RedisConfig:
    url: str = field(default="redis://localhost:6379/0", repr=False)
    prefix: str = "aiogram_extensions"
    connect_timeout: float = 1.0
    socket_timeout: float = 1.0
    max_connections: int = 20

    def __post_init__(self) -> None:
        identifier("prefix", self.prefix)

        seconds("connect_timeout", self.connect_timeout)
        seconds("socket_timeout", self.socket_timeout)

        positive_integer("max_connections", self.max_connections)

        require_instance("url", self.url, str)
        try:
            parsed = urlsplit(self.url)
            valid = (
                parsed.scheme in ("redis", "rediss")
                and bool(parsed.hostname)
                and (parsed.port is None or parsed.port > 0)
                and not parsed.query
                and not parsed.fragment
                and (
                    parsed.path in ("", "/")
                    or (parsed.path[1:].isascii() and parsed.path[1:].isdigit())
                )
            )
        except ValueError:
            valid = False

        if not valid:
            raise ConfigurationError(
                "url",
                "must use redis:// or rediss:// and a database number, without query or fragment",
            )


class RedisLifecycleError(RuntimeError):
    """The manager is not started or has already been closed."""

    def __init__(self) -> None:
        super().__init__("Redis manager must be started and not closed")


class MissingRedisExtraError(ImportError):
    """Redis-backed operations require the optional redis extra."""

    def __init__(self) -> None:
        super().__init__('Install Redis support: pip install "aiogram-extensions[redis]"')


class RedisConnectionManager:
    """Own only clients created here; start creates a client without network I/O."""

    def __init__(self, config: RedisConfig | None = None, *, client: Redis | None = None) -> None:
        self.config: RedisConfig = config if config is not None else RedisConfig()
        require_instance("config", self.config, RedisConfig)

        self._client = client
        self._owns_client = client is None
        self._started = False
        self._closed = False
        self._lock = asyncio.Lock()

    @property
    def owns_client(self) -> bool:
        return self._owns_client

    @property
    def client(self) -> Redis:
        if not self._started or self._closed or self._client is None:
            raise RedisLifecycleError

        return self._client

    async def start(self) -> Self:
        async with self._lock:
            if self._closed:
                raise RedisLifecycleError

            if self._client is None:
                try:
                    from redis.asyncio import Redis  # noqa: PLC0415 -- Optional dependency.
                    from redis.asyncio.retry import Retry  # noqa: PLC0415
                    from redis.backoff import NoBackoff  # noqa: PLC0415
                except ModuleNotFoundError as exc:
                    if exc.name is None or exc.name.split(".")[0] != "redis":
                        raise
                    raise MissingRedisExtraError from None

                # redis-py leaves the public factory's **kwargs unannotated.
                self._client = Redis.from_url(  # pyright: ignore[reportUnknownMemberType]
                    self.config.url,
                    decode_responses=True,
                    socket_connect_timeout=self.config.connect_timeout,
                    socket_timeout=self.config.socket_timeout,
                    max_connections=self.config.max_connections,
                    retry=Retry(NoBackoff(), 0),
                )

            self._started = True

        return self

    async def aclose(self) -> None:
        async with self._lock:
            if self._closed:
                return

            if self._owns_client and self._client is not None:
                await self._client.aclose()

            self._closed = True

    def key(self, namespace: str, *parts: str) -> str:
        identifier("namespace", namespace)

        if not parts:
            raise ConfigurationError("parts", "must be non-empty")

        for part in parts:
            nonempty_string("parts", part)

        return ":".join((self.config.prefix, namespace, *(quote(part, safe="") for part in parts)))

    async def __aenter__(self) -> Self:
        return await self.start()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()


@asynccontextmanager
async def redis_context(
    dispatcher: Dispatcher,
    config: RedisConfig | None = None,
    *,
    client: Redis | None = None,
    key: str = "redis",
) -> AsyncGenerator[RedisConnectionManager, None]:
    """Expose a Redis client through aiogram DI for the lifetime of the context."""
    nonempty_string("key", key)
    if key in dispatcher.workflow_data:
        raise ConfigurationError("key", "is already registered on the dispatcher")

    async with RedisConnectionManager(config, client=client) as manager:
        active_client = manager.client
        dispatcher[key] = active_client

        try:
            yield manager
        finally:
            if dispatcher.get(key) is active_client:
                del dispatcher[key]
