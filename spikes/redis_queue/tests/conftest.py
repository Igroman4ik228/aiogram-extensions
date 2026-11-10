"""Explicit service opt-in and disposable real Redis for the isolated spike."""

import shutil
import socket
import uuid
from collections.abc import Iterator

import pytest
from redis_service import RedisService

IMAGE = "redis@sha256:6aaf3f5e6bc8a592fbfe2cccf19eb36d27c39d12dab4f4b01556b7449e7b1f44"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--run-redis", action="store_true", help="Start a disposable Redis container")
    parser.addoption(
        "--recovery-candidate", action="store_true", help="Test the isolated recovery candidate"
    )
    parser.addoption(
        "--durable", action="store_true", help="Exercise the durable application layer"
    )
    parser.addoption(
        "--transport-wheel", action="store_true", help="Require the pinned local wheel"
    )


@pytest.fixture
def durable(request: pytest.FixtureRequest) -> None:
    if not request.config.getoption("--durable"):
        pytest.skip("Durable pipeline requires explicit --durable")


@pytest.fixture
def recovery_candidate(request: pytest.FixtureRequest) -> None:
    if not any(
        request.config.getoption(flag)
        for flag in (
            "--recovery-candidate",
            "--durable",
            "--transport-wheel",
        )
    ):
        pytest.skip("Recovery contract requires explicit --recovery-candidate")


@pytest.fixture
def transport_wheel(request: pytest.FixtureRequest) -> None:
    if not request.config.getoption("--transport-wheel"):
        pytest.skip("Direct transport checks require the installed --transport-wheel")


@pytest.fixture(scope="session")
def redis_server(request: pytest.FixtureRequest) -> Iterator[RedisService]:
    if not request.config.getoption("--run-redis"):
        pytest.skip("Redis spike requires explicit --run-redis and Docker")
    docker = shutil.which("docker")
    assert docker is not None, "Docker is required for --run-redis"
    name = f"aiogram-extensions-spike-{uuid.uuid4().hex}"

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    service = RedisService(docker, name, f"redis://127.0.0.1:{port}/0")
    service.run(
        "run",
        "--detach",
        "--name",
        name,
        "--publish",
        f"127.0.0.1:{port}:6379",
        IMAGE,
        "redis-server",
        "--save",
        "",
        "--appendonly",
        "yes",
        "--appendfsync",
        "always",
        "--maxmemory-policy",
        "noeviction",
    )
    try:
        service.wait()
        yield service
    finally:
        service.run("rm", "--force", "--volumes", name)


@pytest.fixture(scope="session")
def redis_url(redis_server: RedisService) -> str:
    return redis_server.url
