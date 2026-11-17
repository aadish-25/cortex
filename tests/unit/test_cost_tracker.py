import uuid
import pytest

from backend.db.database import async_session, init_db
from backend.services.orchestration.cost_tracker import (
    CostTracker,
    calculate_model_cost,
    get_model_pricing,
)


def test_calculate_model_cost():
    # Gemini 2.5 Flash has EXACT_PUBLISHED pricing ($0.10 prompt / $0.40 comp per 1M)
    cost, badge, note = calculate_model_cost("gemini/gemini-2.5-flash", "gemini", 1000, 500)
    assert badge == "EXACT_PUBLISHED"
    # prompt: (1000 / 1e6) * 0.10 = 0.00010
    # comp: (500 / 1e6) * 0.40 = 0.00020
    # total = 0.00030
    assert cost == 0.00030
    assert "Official Google AI Studio" in note

    # Groq qwen3.8-27b has ASSUMED_BENCHMARK pricing
    cost_groq, badge_groq, note_groq = calculate_model_cost("groq/qwen/qwen3.8-27b", "groq", 1000, 500)
    assert badge_groq == "ASSUMED_BENCHMARK"
    assert cost_groq == 0.00045
    assert "Groq free preview tier" in note_groq


@pytest.mark.asyncio
async def test_in_memory_cost_tracker_with_model_breakdown():
    tracker = CostTracker()
    test_user = uuid.uuid4()

    # Record 1 Groq call
    log1 = await tracker.record_usage(
        provider="groq",
        model="groq/qwen/qwen3.8-27b",
        prompt_tokens=2000,
        completion_tokens=1000,
        user_id=test_user,
        is_free_tier=True,
    )
    assert log1.prompt_tokens == 2000
    assert log1.completion_tokens == 1000
    assert log1.total_tokens == 3000
    assert log1.is_free_tier is True
    assert log1.cost_badge == "ASSUMED_BENCHMARK"
    assert log1.estimated_cost_usd > 0.0

    # Record 1 OpenRouter call for the same user
    log2 = await tracker.record_usage(
        provider="openrouter",
        model="openrouter/nvidia/nemotron-3.5-lightning:free",
        prompt_tokens=4000,
        completion_tokens=2000,
        user_id=test_user,
        is_free_tier=True,
    )
    assert log2.cost_badge == "ASSUMED_BENCHMARK"

    summary = await tracker.get_summary()
    assert summary["total_tokens"] == 9000
    assert summary["total_estimated_cost_usd"] > 0.0
    assert summary["actual_cash_spend_usd"] == 0.0
    assert summary["is_all_free_tier"] is True

    # Check provider bifurcation and nested model breakdown
    prov_b = summary["provider_breakdown"]
    assert "groq" in prov_b
    assert prov_b["groq"]["total_tokens"] == 3000
    assert "groq/qwen/qwen3.8-27b" in prov_b["groq"]["models"]
    m_info = prov_b["groq"]["models"]["groq/qwen/qwen3.8-27b"]
    assert m_info["cost_badge"] == "ASSUMED_BENCHMARK"
    assert m_info["total_tokens"] == 3000

    # Check user breakdown
    u_b = summary["user_breakdown"]
    assert str(test_user) in u_b
    assert u_b[str(test_user)]["total_tokens"] == 9000


@pytest.mark.asyncio
async def test_db_persistence_token_usage_with_badges():
    await init_db()

    tracker = CostTracker()
    test_user = uuid.uuid4()

    async with async_session() as session:
        log = await tracker.record_usage(
            provider="gemini",
            model="gemini/gemini-2.5-flash",
            prompt_tokens=5000,
            completion_tokens=1000,
            user_id=test_user,
            is_free_tier=True,
            session=session,
        )
        assert log.cost_badge == "EXACT_PUBLISHED"

        # Query summary using DB aggregation
        summary = await tracker.get_summary(session=session)
        assert summary["total_tokens"] >= 6000
        assert "gemini" in summary["provider_breakdown"]
        gem_models = summary["provider_breakdown"]["gemini"]["models"]
        assert "gemini/gemini-2.5-flash" in gem_models
        assert gem_models["gemini/gemini-2.5-flash"]["cost_badge"] == "EXACT_PUBLISHED"
