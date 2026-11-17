import asyncio
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional, Tuple
import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.models.token_usage import TokenUsageLog

logger = logging.getLogger(__name__)

# Model-specific commercial pricing registry per 1,000,000 tokens (USD)
# Allows distinguishing exact published commercial prices from assumed benchmark valuations
MODEL_RATE_REGISTRY: Dict[str, Dict[str, Any]] = {
    "gemini/gemini-2.5-flash": {
        "prompt": 0.10,
        "completion": 0.40,
        "cost_badge": "EXACT_PUBLISHED",
        "pricing_note": "Official Google AI Studio published commercial rate ($0.10 prompt / $0.40 comp per 1M tokens).",
    },
    "gemini-2.5-flash": {
        "prompt": 0.10,
        "completion": 0.40,
        "cost_badge": "EXACT_PUBLISHED",
        "pricing_note": "Official Google AI Studio published commercial rate ($0.10 prompt / $0.40 comp per 1M tokens).",
    },
    "groq/qwen/qwen3.8-27b": {
        "prompt": 0.15,
        "completion": 0.60,
        "cost_badge": "ASSUMED_BENCHMARK",
        "pricing_note": "Served on Groq free preview tier ($0.00 billed). Valued at open-weights market benchmark ($0.15 / $0.60 per 1M tokens).",
    },
    "groq/openai/gpt-oss-120b": {
        "prompt": 0.30,
        "completion": 1.20,
        "cost_badge": "ASSUMED_BENCHMARK",
        "pricing_note": "Served on Groq free preview tier ($0.00 billed). Valued at frontier 120B model benchmark ($0.30 / $1.20 per 1M tokens).",
    },
    "openrouter/nvidia/nemotron-3.5-lightning:free": {
        "prompt": 0.20,
        "completion": 0.80,
        "cost_badge": "ASSUMED_BENCHMARK",
        "pricing_note": "OpenRouter sponsored free tier ($0.00 billed). Valued at 1M-context reasoning benchmark ($0.20 / $0.80 per 1M tokens).",
    },
    "openrouter/google/gemma-4-31b-it:free": {
        "prompt": 0.15,
        "completion": 0.60,
        "cost_badge": "ASSUMED_BENCHMARK",
        "pricing_note": "OpenRouter sponsored free tier ($0.00 billed). Valued at 31B parameter benchmark ($0.15 / $0.60 per 1M tokens).",
    },
    "default": {
        "prompt": 0.15,
        "completion": 0.60,
        "cost_badge": "ASSUMED_BENCHMARK",
        "pricing_note": "Standard open-weights baseline assumption ($0.15 prompt / $0.60 comp per 1M tokens).",
    },
}


def get_model_pricing(model: str, provider: str) -> Tuple[Dict[str, float], str, str]:
    """
    Returns (rates_dict, cost_badge, pricing_note) for a given model and provider.
    Distinguishes exact published pricing from assumed market valuations.
    """
    clean_model = model.strip()
    if clean_model in MODEL_RATE_REGISTRY:
        info = MODEL_RATE_REGISTRY[clean_model]
        return {"prompt": info["prompt"], "completion": info["completion"]}, info["cost_badge"], info["pricing_note"]

    # Try matching without provider prefix
    if "/" in clean_model:
        bare_model = clean_model.split("/", 1)[1]
        if bare_model in MODEL_RATE_REGISTRY:
            info = MODEL_RATE_REGISTRY[bare_model]
            return {"prompt": info["prompt"], "completion": info["completion"]}, info["cost_badge"], info["pricing_note"]

    # Fallback default
    info = MODEL_RATE_REGISTRY["default"]
    return {"prompt": info["prompt"], "completion": info["completion"]}, info["cost_badge"], info["pricing_note"]


def calculate_model_cost(
    model: str,
    provider: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> Tuple[float, str, str]:
    """Calculates commercial equivalent cost and returns (cost_usd, cost_badge, pricing_note)."""
    rates, badge, note = get_model_pricing(model, provider)
    prompt_cost = (prompt_tokens / 1_000_000) * rates["prompt"]
    comp_cost = (completion_tokens / 1_000_000) * rates["completion"]
    return round(prompt_cost + comp_cost, 6), badge, note


class TransparencyBadge:
    def __init__(self, value: str) -> None:
        self.value = value


class CostCalculation:
    def __init__(self, cost: float, cost_badge: str, pricing_note: str) -> None:
        self.cost = cost
        self.cost_badge = cost_badge
        self.pricing_note = pricing_note
        self.transparency_badge = TransparencyBadge(cost_badge)


class CostTracker:
    """
    Sub-millisecond in-memory accumulator and persistent tracking service for LLM
    token consumption, provider & model bifurcation, commercial value estimation,
    badge transparency (EXACT vs ASSUMED), and per-user attribution.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._provider_stats: Dict[str, Dict[str, Any]] = {
            "groq": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "estimated_cost_usd": 0.0, "is_free_tier": True, "models": {}},
            "openrouter": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "estimated_cost_usd": 0.0, "is_free_tier": True, "models": {}},
            "gemini": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "estimated_cost_usd": 0.0, "is_free_tier": True, "models": {}},
        }
        self._user_stats: Dict[str, Dict[str, Any]] = {}

    def calculate_cost(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        is_free_tier: bool = True,
    ) -> CostCalculation:
        """Helper to compute cost and return structured CostCalculation."""
        provider = model.split("/")[0] if "/" in model else "default"
        cost_usd, badge, note = calculate_model_cost(model, provider, prompt_tokens, completion_tokens)
        return CostCalculation(cost=cost_usd, cost_badge=badge, pricing_note=note)

    def reset(self) -> None:
        """Resets in-memory metrics (primarily for test suite isolation)."""
        for p in self._provider_stats:
            self._provider_stats[p] = {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "estimated_cost_usd": 0.0,
                "is_free_tier": True,
                "models": {},
            }
        self._user_stats.clear()

    async def record_usage(
        self,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        user_id: Optional[uuid.UUID] = None,
        is_free_tier: bool = True,
        session: Optional[AsyncSession] = None,
    ) -> TokenUsageLog:
        """
        Records token consumption in-memory and optionally persists a TokenUsageLog row.
        Includes model-specific cost calculation and EXACT vs ASSUMED pricing badges.
        """
        provider_key = provider.lower()
        total_tokens = prompt_tokens + completion_tokens
        est_cost, cost_badge, pricing_note = calculate_model_cost(model, provider_key, prompt_tokens, completion_tokens)

        async with self._lock:
            # Update provider accumulator
            if provider_key not in self._provider_stats:
                self._provider_stats[provider_key] = {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "estimated_cost_usd": 0.0,
                    "is_free_tier": is_free_tier,
                    "models": {},
                }

            prov = self._provider_stats[provider_key]
            prov["prompt_tokens"] += prompt_tokens
            prov["completion_tokens"] += completion_tokens
            prov["total_tokens"] += total_tokens
            prov["estimated_cost_usd"] = round(prov["estimated_cost_usd"] + est_cost, 6)

            # Update per-model breakdown within provider
            if model not in prov["models"]:
                rates, _, _ = get_model_pricing(model, provider_key)
                prov["models"][model] = {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "estimated_cost_usd": 0.0,
                    "cost_badge": cost_badge,
                    "pricing_note": pricing_note,
                    "rates_per_million": rates,
                }
            m_stat = prov["models"][model]
            m_stat["prompt_tokens"] += prompt_tokens
            m_stat["completion_tokens"] += completion_tokens
            m_stat["total_tokens"] += total_tokens
            m_stat["estimated_cost_usd"] = round(m_stat["estimated_cost_usd"] + est_cost, 6)

            # Update per-user accumulator
            user_key = str(user_id) if user_id else "anonymous"
            if user_key not in self._user_stats:
                self._user_stats[user_key] = {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "estimated_cost_usd": 0.0,
                }
            usr = self._user_stats[user_key]
            usr["prompt_tokens"] += prompt_tokens
            usr["completion_tokens"] += completion_tokens
            usr["total_tokens"] += total_tokens
            usr["estimated_cost_usd"] = round(usr["estimated_cost_usd"] + est_cost, 6)

        # Create persistent database model
        log_entry = TokenUsageLog(
            user_id=user_id,
            provider=provider_key,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            estimated_cost_usd=est_cost,
            is_free_tier=is_free_tier,
            cost_badge=cost_badge,
            pricing_note=pricing_note,
            created_at=datetime.now(timezone.utc),
        )

        if session is not None:
            try:
                session.add(log_entry)
                await session.commit()
            except Exception as e:
                logger.error("Failed to commit TokenUsageLog to DB: %s", e)

        return log_entry

    async def get_summary(self, session: Optional[AsyncSession] = None) -> Dict[str, Any]:
        """
        Aggregates token consumption and cost metrics for the Admin Dashboard.
        Provides transparent bifurcation across providers, models, badges, and users.
        """
        if session is not None:
            try:
                stmt = select(TokenUsageLog)
                result = await session.execute(stmt)
                logs: List[TokenUsageLog] = list(result.scalars().all())

                total_tokens = sum(l.total_tokens for l in logs)
                total_est_cost = sum(l.estimated_cost_usd for l in logs)
                is_all_free = all(l.is_free_tier for l in logs)

                provider_breakdown: Dict[str, Dict[str, Any]] = {}
                user_breakdown: Dict[str, Dict[str, Any]] = {}

                for l in logs:
                    # Provider breakdown
                    if l.provider not in provider_breakdown:
                        provider_breakdown[l.provider] = {
                            "prompt_tokens": 0,
                            "completion_tokens": 0,
                            "total_tokens": 0,
                            "estimated_cost_usd": 0.0,
                            "is_free_tier": l.is_free_tier,
                            "models": {},
                        }
                    pb = provider_breakdown[l.provider]
                    pb["prompt_tokens"] += l.prompt_tokens
                    pb["completion_tokens"] += l.completion_tokens
                    pb["total_tokens"] += l.total_tokens
                    pb["estimated_cost_usd"] = round(pb["estimated_cost_usd"] + l.estimated_cost_usd, 6)

                    # Model breakdown inside provider
                    if l.model not in pb["models"]:
                        rates, _, _ = get_model_pricing(l.model, l.provider)
                        pb["models"][l.model] = {
                            "prompt_tokens": 0,
                            "completion_tokens": 0,
                            "total_tokens": 0,
                            "estimated_cost_usd": 0.0,
                            "cost_badge": l.cost_badge,
                            "pricing_note": l.pricing_note,
                            "rates_per_million": rates,
                        }
                    mb = pb["models"][l.model]
                    mb["prompt_tokens"] += l.prompt_tokens
                    mb["completion_tokens"] += l.completion_tokens
                    mb["total_tokens"] += l.total_tokens
                    mb["estimated_cost_usd"] = round(mb["estimated_cost_usd"] + l.estimated_cost_usd, 6)

                    # User breakdown
                    u_key = str(l.user_id) if l.user_id else "anonymous"
                    if u_key not in user_breakdown:
                        user_breakdown[u_key] = {
                            "prompt_tokens": 0,
                            "completion_tokens": 0,
                            "total_tokens": 0,
                            "estimated_cost_usd": 0.0,
                        }
                    ub = user_breakdown[u_key]
                    ub["prompt_tokens"] += l.prompt_tokens
                    ub["completion_tokens"] += l.completion_tokens
                    ub["total_tokens"] += l.total_tokens
                    ub["estimated_cost_usd"] = round(ub["estimated_cost_usd"] + l.estimated_cost_usd, 6)

                return {
                    "total_tokens": total_tokens,
                    "total_estimated_cost_usd": round(total_est_cost, 6),
                    "actual_cash_spend_usd": 0.0 if is_all_free else round(total_est_cost, 6),
                    "is_all_free_tier": is_all_free,
                    "assumed_cost_notice": "Estimated costs include assumed benchmark valuations for preview/free-tier models. See model badges for specific rate attributions.",
                    "provider_breakdown": provider_breakdown,
                    "user_breakdown": user_breakdown,
                }
            except Exception as e:
                logger.warning("Database aggregation failed, falling back to in-memory stats: %s", e)

        # Fallback to in-memory aggregation
        async with self._lock:
            total_tokens = sum(p["total_tokens"] for p in self._provider_stats.values())
            total_est_cost = sum(p["estimated_cost_usd"] for p in self._provider_stats.values())
            is_all_free = all(p.get("is_free_tier", True) for p in self._provider_stats.values())

            return {
                "total_tokens": total_tokens,
                "total_estimated_cost_usd": round(total_est_cost, 6),
                "actual_cash_spend_usd": 0.0 if is_all_free else round(total_est_cost, 6),
                "is_all_free_tier": is_all_free,
                "assumed_cost_notice": "Estimated costs include assumed benchmark valuations for preview/free-tier models. See model badges for specific rate attributions.",
                "provider_breakdown": {k: dict(v) for k, v in self._provider_stats.items()},
                "user_breakdown": {k: dict(v) for k, v in self._user_stats.items()},
            }


cost_tracker = CostTracker()
