"""Enqueue a ping job and wait for the result: ``python -m northforge.worker.ping``."""

from __future__ import annotations

import asyncio
import json
import sys

from arq.connections import create_pool

from northforge.core.config import get_settings
from northforge.core.queue import QUEUE_NAME
from northforge.worker.main import redis_settings_from


async def send_ping(message: str, timeout_seconds: float) -> dict[str, str]:
    settings = get_settings()
    pool = await create_pool(redis_settings_from(settings))
    try:
        job = await pool.enqueue_job("ping", message, _queue_name=QUEUE_NAME)
        if job is None:
            raise RuntimeError("job was not enqueued (duplicate job id)")
        result: dict[str, str] = await job.result(timeout=timeout_seconds)
        return result
    finally:
        await pool.aclose()


def main() -> int:
    message = sys.argv[1] if len(sys.argv) > 1 else "ping"
    try:
        result = asyncio.run(send_ping(message, timeout_seconds=15))
    except TimeoutError:
        print("no worker answered within 15s; is the worker process running?")
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
