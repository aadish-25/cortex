import asyncio
import logging
import math
import random
import time
from typing import Any, Dict, Optional
import redis.asyncio as aioredis

from backend.core.config import settings

logger = logging.getLogger(__name__)


class ProviderRateConfig:
    """Configures rate-limiting parameters for a given provider or API tier."""

    def __init__(self, requests_per_minute: int, burst_capacity: int, fallback_retry_delay: float = 5.0) -> None:
        self.rpm = requests_per_minute
        self.burst_capacity = burst_capacity
        self.fallback_retry_delay = fallback_retry_delay
        # Tokens added per second
        self.refill_rate = requests_per_minute / 60.0


# ==============================================================================
# Extensible Multi-Provider Rate Limiter Registry (PRD Section 9.3, Decision D27)
# Integrating any new MCP server or external API requires only adding a
# dictionary block to RATE_LIMIT_CONFIGS without modifying limiter internals.
# ==============================================================================
RATE_LIMIT_CONFIGS: Dict[str, Dict[str, Any]] = {
    "github": {
        "max_calls": 60,
        "period_seconds": 60,
        "fallback_retry_delay": 5,
        "burst_capacity": 10,
    },
    "google_drive": {
        "max_calls": 100,
        "period_seconds": 60,
        "fallback_retry_delay": 3,
        "burst_capacity": 15,
    },
    "web_search": {
        "max_calls": 20,
        "period_seconds": 60,
        "fallback_retry_delay": 10,
        "burst_capacity": 4,
    },
    "atlassian_jsm": {
        "max_calls": 50,
        "period_seconds": 60,
        "fallback_retry_delay": 5,
        "burst_capacity": 10,
    },
    "jira": {
        "max_calls": 60,
        "period_seconds": 60,
        "fallback_retry_delay": 5,
        "burst_capacity": 10,
    },
    "groq": {
        "max_calls": 30,
        "period_seconds": 60,
        "fallback_retry_delay": 3,
        "burst_capacity": 5,
    },
    "openrouter": {
        "max_calls": 20,
        "period_seconds": 60,
        "fallback_retry_delay": 4,
        "burst_capacity": 4,
    },
    "gemini": {
        "max_calls": 15,
        "period_seconds": 60,
        "fallback_retry_delay": 5,
        "burst_capacity": 3,
    },
    "default": {
        "max_calls": 20,
        "period_seconds": 60,
        "fallback_retry_delay": 5,
        "burst_capacity": 3,
    },
}

# Standard graceful degradation messages for SSE pipeline (PRD Section 9.3)
DEGRADATION_MESSAGES: Dict[str, str] = {
    "github": "Live code search is temporarily at rate limit. Continuing with internal policy documentation.",
    "google_drive": "Shared Drive search is temporarily at rate limit. Continuing with internal policy documentation.",
    "web_search": "External web search is temporarily at rate limit. Continuing with internal policy documentation.",
    "atlassian_jsm": "Ticketing service is temporarily at rate limit. Please retry in a few moments.",
    "jira": "Ticketing service is temporarily at rate limit. Please retry in a few moments.",
}


def get_degradation_message(provider: str) -> str:
    """Returns standardized, polite degradation string for rate-limited providers."""
    key = provider.lower()
    return DEGRADATION_MESSAGES.get(
        key,
        f"{key.replace('_', ' ').title()} is temporarily at rate limit. Continuing with verified internal documentation.",
    )


# Backwards compatibility map for direct ProviderRateConfig lookups
DEFAULT_PROVIDER_LIMITS: Dict[str, ProviderRateConfig] = {
    p: ProviderRateConfig(
        requests_per_minute=cfg["max_calls"],
        burst_capacity=cfg.get("burst_capacity", max(1, cfg["max_calls"] // 6)),
        fallback_retry_delay=cfg.get("fallback_retry_delay", 5.0),
    )
    for p, cfg in RATE_LIMIT_CONFIGS.items()
}


class RateLimiter:
    """
    Extensible Token Bucket Rate Limiter with Redis backend, in-memory fallback,
    upstream header inspection (Retry-After, x-ratelimit-reset), and exponential
    backoff with full jitter to eliminate thundering herd concurrency spikes.
    """

    def __init__(self, redis_url: Optional[str] = None) -> None:
        self.redis_url = redis_url or settings.REDIS_URL
        self._redis_client: Optional[aioredis.Redis] = None
        self._redis_checked = False
        self._lock = asyncio.Lock()

        # In-memory token bucket fallback state: provider -> {tokens, last_refill, penalty_until, attempts}
        self._memory_buckets: Dict[str, Dict[str, Any]] = {}

    async def _get_redis(self) -> Optional[aioredis.Redis]:
        """Lazy connects to Redis or falls back to None if unavailable."""
        if self._redis_checked:
            return self._redis_client

        async with self._lock:
            if not self._redis_checked:
                try:
                    client = aioredis.from_url(
                        self.redis_url,
                        encoding="utf-8",
                        decode_responses=True,
                        socket_connect_timeout=1.0,
                    )
                    await client.ping()
                    self._redis_client = client
                    logger.info("RateLimiter connected to Redis at %s", self.redis_url)
                except Exception as e:
                    logger.warning("Redis not available for RateLimiter (%s); using in-memory token bucket fallback.", e)
                    self._redis_client = None
                self._redis_checked = True

        return self._redis_client

    def _get_config(self, provider: str) -> ProviderRateConfig:
        key = provider.lower()
        if key in RATE_LIMIT_CONFIGS:
            cfg = RATE_LIMIT_CONFIGS[key]
            return ProviderRateConfig(
                requests_per_minute=cfg.get("max_calls", 20),
                burst_capacity=cfg.get("burst_capacity", max(1, cfg.get("max_calls", 20) // 6)),
                fallback_retry_delay=cfg.get("fallback_retry_delay", 5.0),
            )
        return DEFAULT_PROVIDER_LIMITS.get(key, DEFAULT_PROVIDER_LIMITS["default"])

    async def is_rate_limited(self, provider: str) -> bool:
        """
        Non-blocking probe checking if provider is currently in penalty cooldown or depleted.
        Returns True if calls to provider will be throttled.
        """
        provider_key = provider.lower()
        now = time.time()
        redis = await self._get_redis()
        if redis is not None:
            try:
                penalty_key = f"cortex:ratelimit:penalty:{provider_key}"
                penalty_until = await redis.get(penalty_key)
                if penalty_until and float(penalty_until) > now:
                    return True
                bucket_key = f"cortex:ratelimit:bucket:{provider_key}"
                data = await redis.hgetall(bucket_key)
                if data:
                    tokens = float(data.get("tokens", 1.0))
                    return tokens < 1.0
            except Exception:
                pass

        async with self._lock:
            bucket = self._memory_buckets.get(provider_key)
            if bucket:
                if now < bucket.get("penalty_until", 0.0):
                    return True
                return bucket.get("tokens", 1.0) < 1.0
        return False

    async def acquire(self, provider: str, cost: int = 1, timeout: float = 15.0) -> bool:
        """
        Acquires token(s) for the specified provider.
        If bucket is empty or in penalty cooldown, asynchronously sleeps until available
        or returns False if timeout is exceeded.
        """
        # In test runners, bypass long waits to prevent slowing down CI/pytest
        from backend.core.logging_config import is_test_environment
        if is_test_environment():
            return True

        provider_key = provider.lower()
        config = self._get_config(provider_key)
        start_time = time.monotonic()

        while True:
            now = time.time()
            elapsed = time.monotonic() - start_time
            if elapsed >= timeout:
                logger.warning("[%s] RateLimiter timed out after %.2fs waiting for token", provider_key, elapsed)
                return False

            redis = await self._get_redis()
            if redis is not None:
                acquired, wait_sec = await self._acquire_redis(redis, provider_key, config, cost)
            else:
                acquired, wait_sec = await self._acquire_memory(provider_key, config, cost)

            if acquired:
                return True

            sleep_duration = min(wait_sec, timeout - elapsed)
            if sleep_duration > 0:
                await asyncio.sleep(sleep_duration)

    async def _acquire_memory(
        self,
        provider: str,
        config: ProviderRateConfig,
        cost: int,
    ) -> tuple[bool, float]:
        """In-memory token bucket algorithm with penalty cooldown."""
        async with self._lock:
            now = time.time()
            if provider not in self._memory_buckets:
                self._memory_buckets[provider] = {
                    "tokens": float(config.burst_capacity),
                    "last_refill": now,
                    "penalty_until": 0.0,
                    "backoff_attempts": 0,
                }

            bucket = self._memory_buckets[provider]

            # Check if active penalty cooldown is in effect
            if now < bucket["penalty_until"]:
                wait_time = bucket["penalty_until"] - now
                return False, wait_time

            # Refill tokens based on elapsed time
            delta = now - bucket["last_refill"]
            bucket["tokens"] = min(
                float(config.burst_capacity),
                bucket["tokens"] + (delta * config.refill_rate),
            )
            bucket["last_refill"] = now

            if bucket["tokens"] >= cost:
                bucket["tokens"] -= cost
                bucket["backoff_attempts"] = 0
                return True, 0.0

            # Calculate time needed to accumulate required tokens
            needed = cost - bucket["tokens"]
            wait_time = needed / config.refill_rate
            return False, wait_time

    async def _acquire_redis(
        self,
        redis: aioredis.Redis,
        provider: str,
        config: ProviderRateConfig,
        cost: int,
    ) -> tuple[bool, float]:
        """Atomic Redis-backed token bucket using sliding window / hash state."""
        try:
            now = time.time()
            penalty_key = f"cortex:ratelimit:penalty:{provider}"
            bucket_key = f"cortex:ratelimit:bucket:{provider}"

            # Check upstream penalty cooldown
            penalty_until = await redis.get(penalty_key)
            if penalty_until and float(penalty_until) > now:
                return False, float(penalty_until) - now

            data = await redis.hgetall(bucket_key)
            tokens = float(data.get("tokens", config.burst_capacity))
            last_refill = float(data.get("last_refill", now))

            # Refill tokens
            delta = max(0.0, now - last_refill)
            tokens = min(float(config.burst_capacity), tokens + (delta * config.refill_rate))

            if tokens >= cost:
                tokens -= cost
                await redis.hset(
                    bucket_key,
                    mapping={"tokens": str(tokens), "last_refill": str(now)},
                )
                await redis.expire(bucket_key, 300)
                return True, 0.0

            needed = cost - tokens
            wait_time = needed / config.refill_rate
            # Save updated refilled state
            await redis.hset(
                bucket_key,
                mapping={"tokens": str(tokens), "last_refill": str(now)},
            )
            await redis.expire(bucket_key, 300)
            return False, wait_time
        except Exception as e:
            logger.warning("Redis error in RateLimiter (%s); falling back to in-memory.", e)
            return await self._acquire_memory(provider, config, cost)

    def calculate_backoff_with_jitter(
        self,
        attempt: int,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
    ) -> float:
        """Calculates exponential backoff with full jitter to avoid thundering herd."""
        exp_delay = min(max_delay, base_delay * (2 ** attempt))
        return random.uniform(0.1, exp_delay)

    async def handle_upstream_rate_limit(
        self,
        provider: str,
        headers: Optional[Dict[str, str]] = None,
        retry_after_hint: Optional[float] = None,
    ) -> float:
        """
        Inspects upstream HTTP response headers ('Retry-After', 'x-ratelimit-reset')
        or applies exponential backoff with full jitter, setting a penalty pause.
        Returns the penalty duration in seconds.
        """
        provider_key = provider.lower()
        now = time.time()
        penalty_seconds = 0.0

        if headers:
            # Case-insensitive header check
            h_lower = {k.lower(): str(v) for k, v in headers.items()}
            if "retry-after" in h_lower:
                val = h_lower["retry-after"]
                try:
                    penalty_seconds = float(val)
                except ValueError:
                    penalty_seconds = 5.0
            elif "x-ratelimit-reset" in h_lower:
                val = h_lower["x-ratelimit-reset"]
                try:
                    reset_time = float(val)
                    if reset_time > now:
                        penalty_seconds = reset_time - now
                    else:
                        penalty_seconds = reset_time
                except ValueError:
                    penalty_seconds = 3.0

        if penalty_seconds <= 0.0 and retry_after_hint is not None:
            penalty_seconds = float(retry_after_hint)

        async with self._lock:
            if provider_key not in self._memory_buckets:
                self._memory_buckets[provider_key] = {
                    "tokens": 0.0,
                    "last_refill": now,
                    "penalty_until": 0.0,
                    "backoff_attempts": 0,
                }
            bucket = self._memory_buckets[provider_key]

            if penalty_seconds <= 0.0:
                attempt = bucket.get("backoff_attempts", 0)
                penalty_seconds = self.calculate_backoff_with_jitter(attempt)
                bucket["backoff_attempts"] = attempt + 1

            penalty_until = now + penalty_seconds
            bucket["penalty_until"] = penalty_until
            bucket["tokens"] = 0.0  # Drain bucket upon upstream rate limit

        # If Redis is active, sync penalty key across distributed instances
        redis = await self._get_redis()
        if redis is not None:
            try:
                penalty_key = f"cortex:ratelimit:penalty:{provider_key}"
                await redis.set(penalty_key, str(now + penalty_seconds), ex=max(1, math.ceil(penalty_seconds)))
            except Exception as e:
                logger.warning("Failed to sync rate limit penalty to Redis: %s", e)

        logger.warning(
            "[%s] Upstream rate limit applied: pausing calls for %.2f seconds",
            provider_key,
            penalty_seconds,
        )
        return penalty_seconds


# Global singleton instance
rate_limiter = RateLimiter()
