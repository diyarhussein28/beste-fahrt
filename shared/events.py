"""Lightweight event bus on Redis Streams — decouples Collector, Matching
Engine, Dispatcher and Return Finder (القسم 3.1) so any one of them can be
restarted or replaced without the others noticing.
"""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, AsyncIterator

import redis.asyncio as redis

from shared.config import get_secrets

STREAM_MAXLEN = 10_000


@lru_cache
def get_redis() -> redis.Redis:
    # socket_timeout=None is required for blocking commands (XREADGROUP ...
    # BLOCK): with a finite client-side read timeout, the client can time out
    # waiting on the socket before the server's own BLOCK window elapses and
    # responds, even though nothing is actually wrong with the connection.
    return redis.from_url(get_secrets().redis_url, decode_responses=True, socket_timeout=None, socket_connect_timeout=5)


async def publish(stream: str, payload: dict[str, Any] | str) -> str:
    data = payload if isinstance(payload, str) else json.dumps(payload)
    return await get_redis().xadd(stream, {"data": data}, maxlen=STREAM_MAXLEN, approximate=True)


async def consume(
    stream: str, group: str, consumer: str, block_ms: int = 5000
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Yields (message_id, payload) pairs from a consumer group, creating the
    group on first use. Caller is responsible for acking via `ack`.
    """
    r = get_redis()
    try:
        await r.xgroup_create(stream, group, id="0", mkstream=True)
    except redis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise

    while True:
        resp = await r.xreadgroup(group, consumer, {stream: ">"}, count=10, block=block_ms)
        if not resp:
            continue
        for _stream_name, messages in resp:
            for message_id, fields in messages:
                payload = json.loads(fields["data"]) if fields.get("data", "").startswith("{") else fields.get("data")
                yield message_id, payload


async def ack(stream: str, group: str, message_id: str) -> None:
    await get_redis().xack(stream, group, message_id)
