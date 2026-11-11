"""Redis lifecycle ownership without a running Redis service."""

import asyncio
import socket
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from redis.asyncio import Redis

from aiogram_extensions.redis import RedisConfig, RedisConnectionManager, RedisLifecycleError


@pytest.mark.parametrize(
    ("settings", "error"),
    [
        ({"url": "http://localhost"}, "url"),
        ({"url": "redis://localhost:bad"}, "url"),
        ({"url": "redis://localhost?socket_timeout=0"}, "url"),
        ({"prefix": "a:b"}, "prefix"),
        ({"socket_timeout": 0}, "socket_timeout"),
        ({"connect_timeout": float("nan")}, "connect_timeout"),
        ({"max_connections": True}, "max_connections"),
    ],
)
def test_config_rejects_invalid_redis_settings(settings: dict[str, Any], error: str) -> None:
    with pytest.raises(ValueError, match=error):
        RedisConfig(**settings)


@pytest.mark.parametrize("url", [None, 123, "redis://localhost/no-db", "redis://localhost:70000"])
def test_config_rejects_invalid_url(url: Any) -> None:
    with pytest.raises(ValueError, match="url"):
        RedisConfig(url=url)


async def test_borrowed_client_is_returned_and_never_closed() -> None:
    client = AsyncMock(spec=Redis)
    manager = RedisConnectionManager(client=client)

    async with manager:
        assert manager.client is client
        assert not manager.owns_client

    client.aclose.assert_not_awaited()


async def test_owned_client_is_created_lazily_and_closed_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = AsyncMock(spec=Redis)
    factory = Mock(return_value=client)
    monkeypatch.setattr(Redis, "from_url", factory)
    manager = RedisConnectionManager()

    factory.assert_not_called()

    async with manager:
        assert manager.client is client
        assert manager.owns_client
        await manager.start()
    await manager.aclose()

    factory.assert_called_once()
    assert factory.call_args.args[0] == "redis://localhost:6379/0"
    client.aclose.assert_awaited_once()


async def test_lifecycle_rejects_access_before_start_and_restart_after_close() -> None:
    manager = RedisConnectionManager()

    with pytest.raises(RedisLifecycleError):
        _ = manager.client
    await manager.aclose()
    with pytest.raises(RedisLifecycleError):
        await manager.start()
    with pytest.raises(RedisLifecycleError):
        _ = manager.client


async def test_owned_client_closes_after_body_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    close = AsyncMock()
    client = AsyncMock(spec=Redis, aclose=close)
    monkeypatch.setattr(Redis, "from_url", Mock(return_value=client))
    with pytest.raises(LookupError):
        async with RedisConnectionManager():
            raise LookupError
    close.assert_awaited_once()


async def test_failed_close_can_be_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    close = AsyncMock(side_effect=[OSError("disconnect"), None])
    client = AsyncMock(spec=Redis, aclose=close)
    monkeypatch.setattr(Redis, "from_url", Mock(return_value=client))
    manager = RedisConnectionManager()
    await manager.start()

    with pytest.raises(OSError, match="disconnect"):
        await manager.aclose()
    assert manager.client is client

    await manager.aclose()

    assert close.await_count == 2


async def test_concurrent_close_releases_owned_client_once(monkeypatch: pytest.MonkeyPatch) -> None:
    close = AsyncMock()
    client = AsyncMock(spec=Redis, aclose=close)
    monkeypatch.setattr(Redis, "from_url", Mock(return_value=client))
    manager = RedisConnectionManager()

    await asyncio.gather(manager.start(), manager.start())
    await asyncio.gather(manager.aclose(), manager.aclose())

    close.assert_awaited_once()


async def test_real_client_lifecycle_never_opens_a_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    connect = Mock(side_effect=AssertionError("network"))
    monkeypatch.setattr(socket.socket, "connect", connect)

    async with RedisConnectionManager() as manager:
        assert isinstance(manager.client, Redis)

    connect.assert_not_called()


def test_namespaced_keys_escape_separators_without_collisions() -> None:
    manager = RedisConnectionManager(RedisConfig(prefix="bot"))

    assert manager.key("rate_limit", "a:b", "☃") == "bot:rate_limit:a%3Ab:%E2%98%83"
    assert manager.key("rate_limit", "a:b") != manager.key("rate_limit", "a", "b")
    assert manager.key("rate_limit", "a:b") != manager.key("rate_limit", "a%3Ab")
    assert manager.key("rate_limit", "key") != manager.key("messaging", "key")


@pytest.mark.parametrize("parts", [(), ("",), (" ",)])
def test_empty_key_parts_are_rejected(parts: tuple[str, ...]) -> None:
    with pytest.raises(ValueError, match="parts"):
        RedisConnectionManager().key("rate_limit", *parts)


def test_config_repr_hides_credentials() -> None:
    config = RedisConfig(url="redis://user:private-password@localhost:6379/0")

    description = repr(config)

    assert "private-password" not in description
    assert "redis://" not in description
