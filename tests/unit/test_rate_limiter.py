import asyncio
import time
import pytest
import redis.asyncio as aioredis

from backend.core.config import settings
from backend.core.rate_limiter import RateLimiter, ProviderRateConfig, DEFAULT_PROVIDER_LIMITS


@pytest.mark.asyncio
async def test_provider_rate_config_defaults():
    assert "groq" in DEFAULT_PROVIDER_LIMITS
    assert "openrouter" in DEFAULT_PROVIDER_LIMITS
    assert "gemini" in DEFAULT_PROVIDER_LIMITS

    groq_cfg = DEFAULT_PROVIDER_LIMITS["groq"]
    assert groq_cfg.rpm == 30
    assert groq_cfg.burst_capacity == 5
    assert groq_cfg.refill_rate == 0.5


@pytest.mark.asyncio
async def test_in_memory_token_bucket_acquire():
    # Use invalid redis url to force in-memory fallback
    limiter = RateLimiter(redis_url="redis://localhost:9999/0")
    config = ProviderRateConfig(requests_per_minute=60, burst_capacity=2)

    # First acquire succeeds immediately
    acq1, wait1 = await limiter._acquire_memory("test_provider", config, cost=1)
    assert acq1 is True
    assert wait1 == 0.0

    # Second acquire succeeds immediately (burst capacity = 2)
    acq2, wait2 = await limiter._acquire_memory("test_provider", config, cost=1)
    assert acq2 is True
    assert wait2 == 0.0

    # Third acquire fails because tokens are exhausted
    acq3, wait3 = await limiter._acquire_memory("test_provider", config, cost=1)
    assert acq3 is False
    assert wait3 > 0.0


@pytest.mark.asyncio
async def test_upstream_retry_after_header_handling():
    limiter = RateLimiter(redis_url="redis://localhost:9999/0")
    headers = {"retry-after": "2.5"}

    penalty = await limiter.handle_upstream_rate_limit("groq", headers=headers)
    assert penalty == 2.5

    # Memory bucket must reflect active penalty
    bucket = limiter._memory_buckets["groq"]
    assert bucket["penalty_until"] > time.time()

    # Further acquire attempts must be rejected during penalty
    config = DEFAULT_PROVIDER_LIMITS["groq"]
    acq, wait = await limiter._acquire_memory("groq", config, cost=1)
    assert acq is False
    assert wait > 0.0


@pytest.mark.asyncio
async def test_upstream_x_ratelimit_reset_handling():
    limiter = RateLimiter(redis_url="redis://localhost:9999/0")
    reset_future = time.time() + 4.0
    headers = {"x-ratelimit-reset": str(reset_future)}

    penalty = await limiter.handle_upstream_rate_limit("openrouter", headers=headers)
    assert 3.0 <= penalty <= 5.0


@pytest.mark.asyncio
async def test_exponential_backoff_with_jitter():
    limiter = RateLimiter()
    delays = [limiter.calculate_backoff_with_jitter(attempt=i, base_delay=1.0) for i in range(5)]
    # Each delay must be within positive jitter bounds
    for d in delays:
        assert 0.1 <= d <= 30.0


@pytest.mark.asyncio
async def test_redis_token_bucket_live_docker():
    # Verify live Docker Redis integration
    limiter = RateLimiter(redis_url=settings.REDIS_URL)
    redis_client = await limiter._get_redis()

    if redis_client is not None:
        provider = f"test_redis_{int(time.time())}"
        config = ProviderRateConfig(requests_per_minute=120, burst_capacity=3)

        acq1, wait1 = await limiter._acquire_redis(redis_client, provider, config, cost=1)
        assert acq1 is True
        assert wait1 == 0.0

        acq2, wait2 = await limiter._acquire_redis(redis_client, provider, config, cost=1)
        assert acq2 is True
        assert wait2 == 0.0

        # Clean up test keys
        await redis_client.delete(f"cortex:ratelimit:bucket:{provider}")
