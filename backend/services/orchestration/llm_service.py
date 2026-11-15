import asyncio
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple, Type, TypeVar, cast

from pydantic import BaseModel
import litellm
from litellm import ModelResponse

from backend.core.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class KeyPool:
    """
    Manages an in-memory round-robin pool of API keys for a specific provider.
    Automatically rotates keys upon rate limits (429) or failures, and blacklists
    permanently invalid keys (:NOTWORKING).
    """

    def __init__(self, provider_name: str, raw_keys_str: str, single_fallback_key: str = "") -> None:
        self.provider_name = provider_name
        self._lock = asyncio.Lock()
        self._current_idx = 0
        self.valid_keys: List[str] = []
        self.dead_keys: set[str] = set()

        # Parse keys from comma-separated string, stripping any surrounding quotes
        raw_list = [k.strip(' "\'') for k in raw_keys_str.split(",") if k.strip(' "\'')]
        if not raw_list and single_fallback_key:
            raw_list = [single_fallback_key.strip(' "\'')]

        for item in raw_list:
            if item.endswith(":NOTWORKING"):
                clean = item.replace(":NOTWORKING", "").strip()
                self.dead_keys.add(clean)
            else:
                self.valid_keys.append(item)

    def has_keys(self) -> bool:
        return len(self.valid_keys) > 0

    def get_current_key(self) -> Optional[str]:
        if not self.valid_keys:
            return None
        return self.valid_keys[self._current_idx % len(self.valid_keys)]

    def rotate_key(self, failed_key: Optional[str] = None, is_permanent: bool = False) -> Optional[str]:
        """Rotates to the next available key in the pool."""
        if failed_key and is_permanent:
            logger.warning("[%s] Marking key %s... as NOTWORKING", self.provider_name, failed_key[:8])
            self.dead_keys.add(failed_key)
            if failed_key in self.valid_keys:
                self.valid_keys.remove(failed_key)

        if not self.valid_keys:
            logger.error("[%s] All API keys in pool exhausted or dead!", self.provider_name)
            return None

        self._current_idx = (self._current_idx + 1) % len(self.valid_keys)
        new_key = self.valid_keys[self._current_idx]
        logger.info("[%s] Rotated to next key (active pool size=%d)", self.provider_name, len(self.valid_keys))
        return new_key


class LLMService:
    """
    High-availability, multi-provider LLM gateway powered by LiteLLM.
    Strict Priority Cascade:
        1. Groq (Fastest inference & low latency)
        2. OpenRouter (Multi-model free tier backup)
        3. Gemini (Google AI Studio fallback)

    Includes automatic in-memory key rotation and zero-downtime failovers.
    """

    def __init__(self) -> None:
        # Suppress noisy telemetry
        os.environ["LITELLM_TELEMETRY"] = "False"

        # Initialize Key Pools per provider
        self.groq_pool = KeyPool("Groq", settings.GROQ_API_KEYS, settings.GROQ_API_KEY)
        self.openrouter_pool = KeyPool("OpenRouter", settings.OPENROUTER_API_KEYS, settings.OPENROUTER_API_KEY)
        self.gemini_pool = KeyPool("Gemini", settings.GEMINI_API_KEYS, settings.GEMINI_API_KEY)

    def is_configured(self) -> bool:
        """Returns True if at least one provider has active keys."""
        return self.groq_pool.has_keys() or self.openrouter_pool.has_keys() or self.gemini_pool.has_keys()

    def _get_provider_cascade(self) -> List[Tuple[str, KeyPool, str]]:
        """
        Returns the ordered priority cascade:
        1. Groq -> 2. OpenRouter -> 3. Gemini
        Each tuple contains (provider_name, key_pool, default_model).
        """
        return [
            ("groq", self.groq_pool, settings.GROQ_DEFAULT_MODEL),
            ("openrouter", self.openrouter_pool, settings.OPENROUTER_DEFAULT_MODEL),
            ("gemini", self.gemini_pool, settings.GEMINI_DEFAULT_MODEL),
        ]

    async def generate_text(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.2,
    ) -> str:
        """
        Executes text generation cascading through Groq -> OpenRouter -> Gemini.
        Rotates keys on rate limit (429) and fails over seamlessly.
        """
        if not self.is_configured():
            logger.warning("No LLM API keys configured. LLM text generation unavailable.")
            return ""

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        cascade = self._get_provider_cascade()

        for provider_name, pool, default_model in cascade:
            if not pool.has_keys():
                continue

            target_model = model or default_model
            max_attempts = len(pool.valid_keys)

            for _ in range(max_attempts):
                active_key = pool.get_current_key()
                if not active_key:
                    break

                try:
                    raw_response = await litellm.acompletion(
                        model=target_model,
                        messages=messages,
                        api_key=active_key,
                        temperature=temperature,
                    )
                    response = cast(ModelResponse, raw_response)
                    choice = response.choices[0]
                    content = getattr(choice.message, "content", "") or ""
                    return content.strip()
                except Exception as exc:
                    err_str = str(exc).lower()
                    is_perm = any(code in err_str for code in ["401", "403", "unauthorized", "invalid_api_key"])
                    logger.warning(
                        "[%s] Key failed (%s). Attempting key rotation.",
                        provider_name,
                        "Permanent Auth Error" if is_perm else "Rate Limit / Provider Error",
                    )
                    pool.rotate_key(failed_key=active_key, is_permanent=is_perm)

            logger.warning("[%s] All keys in pool exhausted. Cascading to next provider.", provider_name)

        raise RuntimeError("All configured LLM providers (Groq, OpenRouter, Gemini) failed.")

    async def generate_structured(
        self,
        prompt: str,
        response_model: Type[T],
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.0,
    ) -> Optional[T]:
        """
        Executes schema-constrained structured output cascading through Groq -> OpenRouter -> Gemini.
        Guarantees that the returned object is a strictly validated instance of response_model.
        """
        if not self.is_configured():
            logger.warning("No LLM API keys configured. Structured generation unavailable.")
            return None

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        cascade = self._get_provider_cascade()

        for provider_name, pool, default_model in cascade:
            if not pool.has_keys():
                continue

            target_model = model or default_model
            max_attempts = len(pool.valid_keys)

            for _ in range(max_attempts):
                active_key = pool.get_current_key()
                if not active_key:
                    break

                try:
                    raw_response = await litellm.acompletion(
                        model=target_model,
                        messages=messages,
                        api_key=active_key,
                        response_format=response_model,
                        temperature=temperature,
                    )
                    response = cast(ModelResponse, raw_response)
                    choice = response.choices[0]
                    raw_content = getattr(choice.message, "content", "") or "{}"

                    if raw_content.startswith("```"):
                        raw_content = re.sub(r"^```(?:json)?\n|\n```$", "", raw_content.strip())

                    return response_model.model_validate_json(raw_content)
                except Exception as exc:
                    err_str = str(exc).lower()
                    is_perm = any(code in err_str for code in ["401", "403", "unauthorized", "invalid_api_key"])
                    logger.warning(
                        "[%s] Structured generation failed with key (%s). Rotating key.",
                        provider_name,
                        "Permanent Auth Error" if is_perm else "Error/RateLimit",
                    )
                    pool.rotate_key(failed_key=active_key, is_permanent=is_perm)

            logger.warning("[%s] All structured attempts exhausted. Cascading to next provider.", provider_name)

        return None


llm_service = LLMService()
