"""Unit tests for individual readiness checks with simulated failures.

Linux raises ``redis.exceptions.ConnectionError`` immediately on a refused
connection, whereas Windows surfaces a timeout; both must be handled.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import redis.exceptions

from northforge.core.health import check_redis, check_worker


def _redis_raising(exc: Exception) -> AsyncMock:
    client = AsyncMock()
    client.ping.side_effect = exc
    client.get.side_effect = exc
    return client


async def test_check_redis_handles_redis_connection_error() -> None:
    client = _redis_raising(redis.exceptions.ConnectionError("Connection refused"))

    result = await check_redis(client, timeout_seconds=0.5)

    assert result.status == "error"
    assert result.detail == "connection failed"


async def test_check_worker_handles_redis_connection_error() -> None:
    client = _redis_raising(redis.exceptions.ConnectionError("Connection refused"))

    result = await check_worker(client, timeout_seconds=0.5)

    assert result.status == "error"
    assert result.detail == "redis unreachable"


async def test_check_redis_handles_timeout() -> None:
    client = _redis_raising(TimeoutError())

    result = await check_redis(client, timeout_seconds=0.5)

    assert result.status == "error"


async def test_check_worker_reports_missing_heartbeat() -> None:
    client = AsyncMock()
    client.get.return_value = None

    result = await check_worker(client, timeout_seconds=0.5)

    assert result.status == "unavailable"
    assert result.detail == "no recent worker heartbeat"


async def test_check_worker_returns_heartbeat_detail() -> None:
    client = AsyncMock()
    client.get.return_value = b"Sep-15 16:01:02 j_complete=1"

    result = await check_worker(client, timeout_seconds=0.5)

    assert result.status == "ok"
    assert result.detail == "Sep-15 16:01:02 j_complete=1"
