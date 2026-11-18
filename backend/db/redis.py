import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple
import redis.asyncio as aioredis

from backend.core.config import settings

logger = logging.getLogger(__name__)

# Fallback in-memory buffer if Redis is unreachable: {stream_key: [{"id": ..., "event": ..., "data": ...}]}
_IN_MEMORY_STREAM_FALLBACK: Dict[str, List[Dict[str, str]]] = {}
_fallback_id_counter = 0


class RedisManager:
    """
    Asynchronous Redis connection manager for Cortex.
    Powers rate-limiting, session locks, and Redis Stream replay buffers for SSE events.
    """

    def __init__(self, redis_url: Optional[str] = None) -> None:
        self.redis_url = redis_url or settings.REDIS_URL
        self._client: Optional[aioredis.Redis] = None
        self._lock = asyncio.Lock()
        self._is_connected = False

    async def get_client(self) -> Optional[aioredis.Redis]:
        """Lazy initializes and validates the Redis connection."""
        current_loop = asyncio.get_running_loop()
        if self._is_connected and self._client is not None:
            pool = getattr(self._client, "connection_pool", None)
            pool_loop = getattr(pool, "_loop", None)
            if pool_loop is not None and pool_loop != current_loop:
                self._client = None
                self._is_connected = False
            else:
                return self._client

        async with self._lock:
            if self._is_connected and self._client is not None:
                pool = getattr(self._client, "connection_pool", None)
                pool_loop = getattr(pool, "_loop", None)
                if pool_loop is not None and pool_loop != current_loop:
                    self._client = None
                    self._is_connected = False
                else:
                    return self._client
            try:
                client = aioredis.from_url(
                    self.redis_url,
                    encoding="utf-8",
                    decode_responses=True,
                    socket_connect_timeout=2.0,
                )
                await client.ping()
                self._client = client
                self._is_connected = True
                logger.info("Connected to Redis at %s", self.redis_url)
                return self._client
            except Exception as e:
                logger.warning("Redis unavailable at %s (%s); utilizing in-memory stream fallback.", self.redis_url, e)
                self._client = None
                self._is_connected = False
                return None

    async def close(self) -> None:
        """Closes the Redis client connection."""
        async with self._lock:
            if self._client is not None:
                try:
                    await self._client.aclose()
                except Exception as e:
                    logger.warning("Error closing Redis connection: %s", e)
                finally:
                    self._client = None
                    self._is_connected = False

    async def append_stream_event(
        self,
        stream_key: str,
        event_type: str,
        data: str,
        ttl_seconds: int = 600,
    ) -> str:
        """
        Appends a typed SSE event to the Redis Stream (Decision D17).
        Sets a 10-minute TTL on the stream key for replay caching.
        Returns the generated event ID (e.g. '1710000000000-0').
        """
        global _fallback_id_counter
        client = await self.get_client()

        if client is not None:
            try:
                event_id = await client.xadd(
                    name=stream_key,
                    fields={"event": event_type, "data": data},
                    maxlen=1000,
                    approximate=True,
                )
                await client.expire(stream_key, ttl_seconds)
                return str(event_id)
            except Exception as e:
                logger.warning("Failed to append event to Redis Stream %s (%s); falling back to memory.", stream_key, e)

        # In-memory fallback
        _fallback_id_counter += 1
        fake_id = f"{_fallback_id_counter}-0"
        if stream_key not in _IN_MEMORY_STREAM_FALLBACK:
            _IN_MEMORY_STREAM_FALLBACK[stream_key] = []
        _IN_MEMORY_STREAM_FALLBACK[stream_key].append(
            {"id": fake_id, "event": event_type, "data": data}
        )
        return fake_id

    async def read_stream_events_after(
        self,
        stream_key: str,
        last_event_id: str,
        count: int = 200,
    ) -> List[Tuple[str, str, str]]:
        """
        Reads events strictly after `last_event_id` to replay missed SSE frames on reconnect.
        Returns list of (event_id, event_type, data_json).
        """
        client = await self.get_client()
        if client is not None:
            try:
                # xread with {stream_key: last_event_id} returns items with IDs > last_event_id
                results = await client.xread(
                    streams={stream_key: last_event_id},
                    count=count,
                )
                events: List[Tuple[str, str, str]] = []
                if results:
                    for _stream, message_list in results:
                        for msg_id, fields in message_list:
                            events.append((str(msg_id), fields.get("event", "message"), fields.get("data", "")))
                return events
            except Exception as e:
                logger.warning("Error reading from Redis Stream %s (%s)", stream_key, e)

        # In-memory fallback
        events: List[Tuple[str, str, str]] = []
        stored = _IN_MEMORY_STREAM_FALLBACK.get(stream_key, [])
        found_last = False
        for item in stored:
            if found_last or last_event_id in ("0", "0-0", ""):
                events.append((item["id"], item["event"], item["data"]))
            elif item["id"] == last_event_id:
                found_last = True
        return events

    async def iter_stream_events_after(
        self,
        stream_key: str,
        last_event_id: str,
        *,
        initial_wait_seconds: float = 4.0,
        idle_timeout_seconds: float = 45.0,
        block_ms: int = 1000,
    ) -> AsyncIterator[Tuple[str, str, str]]:
        """Replay and tail a stream until a terminal event or an idle timeout.

        Unlike a one-shot XREAD, this keeps following the original generation so
        reconnecting clients receive tokens produced after the replay begins.
        """
        terminal_events = {"done", "error", "interrupted", "session_terminated"}
        client = await self.get_client()
        started = time.monotonic()
        last_activity = started
        cursor = last_event_id or "0-0"
        yielded_any = False

        if client is not None:
            while time.monotonic() - last_activity < idle_timeout_seconds:
                try:
                    batches = await client.xread(
                        streams={stream_key: cursor},
                        count=200,
                        block=block_ms,
                    )
                except Exception as exc:
                    logger.warning("Redis stream tail failed for %s (%s)", stream_key, exc)
                    break
                if not batches:
                    if not yielded_any and time.monotonic() - started >= initial_wait_seconds:
                        break
                    continue
                for _stream, entries in batches:
                    for event_id, fields in entries:
                        cursor = str(event_id)
                        event_type = fields.get("event", "message")
                        data = fields.get("data", "")
                        yielded_any = True
                        last_activity = time.monotonic()
                        yield cursor, event_type, data
                        if event_type in terminal_events:
                            return
            if not yielded_any:
                yield "0-0", "error", json.dumps({
                    "code": "STREAM_EXPIRED_OR_UNAVAILABLE",
                    "retryable": False,
                    "message": "The original response stream is no longer available. Reload the conversation to view its saved state.",
                })
            else:
                yield cursor, "error", json.dumps({
                    "code": "STREAM_IDLE",
                    "retryable": True,
                    "message": "The response stream stopped producing events. Reconnect using the last received event ID.",
                })
            return

        # Process-local fallback used only while Redis is unavailable.
        while time.monotonic() - last_activity < idle_timeout_seconds:
            stored = _IN_MEMORY_STREAM_FALLBACK.get(stream_key, [])
            new_items = []
            found_cursor = cursor in ("0", "0-0", "")
            for item in stored:
                if found_cursor:
                    new_items.append(item)
                elif item["id"] == cursor:
                    found_cursor = True
            if new_items:
                for item in new_items:
                    cursor = item["id"]
                    yielded_any = True
                    last_activity = time.monotonic()
                    yield cursor, item["event"], item["data"]
                    if item["event"] in terminal_events:
                        return
            elif not yielded_any and time.monotonic() - started >= initial_wait_seconds:
                break
            await asyncio.sleep(0.25)
        if not yielded_any:
            yield "0-0", "error", json.dumps({
                "code": "STREAM_EXPIRED_OR_UNAVAILABLE",
                "retryable": False,
                "message": "The original response stream is no longer available. Reload the conversation to view its saved state.",
            })
        else:
            yield cursor, "error", json.dumps({
                "code": "STREAM_IDLE",
                "retryable": True,
                "message": "The response stream stopped producing events. Reconnect using the last received event ID.",
            })



redis_manager = RedisManager()



def get_redis_client() -> Optional[aioredis.Redis]:
    """
    Returns the active Redis client from the global redis_manager, if connected.
    Used for synchronous dependency injection or client checking.
    """
    return redis_manager._client
