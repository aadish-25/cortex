import datetime
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
from typing import Any, Optional

from backend.core.config import BASE_DIR

LOGS_DIR = BASE_DIR / "logs"
GENERAL_LOGS_DIR = LOGS_DIR / "general"
TEST_LOGS_DIR = LOGS_DIR / "test"

# Ensure log directories exist
GENERAL_LOGS_DIR.mkdir(parents=True, exist_ok=True)
TEST_LOGS_DIR.mkdir(parents=True, exist_ok=True)

_is_configured = False


def is_test_environment() -> bool:
    """Returns True if the current process is running under a test runner (pytest/eval)."""
    return (
        "pytest" in sys.modules
        or "PYTEST_CURRENT_TEST" in os.environ
        or os.environ.get("CORTEX_ENV") == "test"
        or os.environ.get("CORTEX_LOG_MODE") == "test"
    )


def get_active_log_dir() -> Path:
    """Selects test or general log directory based on execution context."""
    return TEST_LOGS_DIR if is_test_environment() else GENERAL_LOGS_DIR


def setup_logging(mode: Optional[str] = None) -> None:
    """
    Initializes enterprise logging with:
    1. Console Handler: Clean, readable logs streamed to terminal/run.py
    2. Main App/Test File Handler: Rotating log of all system warnings, errors, info
    3. Dedicated LLM File Handler: Isolated log recording every single prompt, token count, and LLM output
    """
    global _is_configured
    if _is_configured and mode is None:
        return

    target_dir = TEST_LOGS_DIR if (mode == "test" or is_test_environment()) else GENERAL_LOGS_DIR
    main_log_name = "test_runs.log" if target_dir == TEST_LOGS_DIR else "app.log"
    main_log_path = target_dir / main_log_name

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # Avoid duplicate handlers if reconfigured
    for handler in list(root_logger.handlers):
        if isinstance(handler, RotatingFileHandler):
            root_logger.removeHandler(handler)

    # Standard rotating file handler (10 MB per file, up to 5 backups)
    file_formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | [%(name)s:%(lineno)d] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = RotatingFileHandler(
        str(main_log_path),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(file_formatter)
    root_logger.addHandler(file_handler)

    # Suppress verbose noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("litellm").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    _is_configured = True


def log_llm_interaction(
    provider: str,
    model: str,
    user_prompt: str,
    response_content: Any,
    system_prompt: Optional[str] = None,
    api_key_masked: Optional[str] = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    latency_ms: float = 0.0,
    status: str = "SUCCESS",
    caller: Optional[str] = None,
    estimated_cost: float = 0.0,
    transparency_badge: str = "EXACT_PUBLISHED",
) -> None:
    """
    Industry-standard structured logger for LLM calls.
    Writes full prompt, response, latency, token consumption, and cost metadata
    to logs/test/test_llm_calls.log or logs/general/llm_calls.log.
    """
    target_dir = get_active_log_dir()
    llm_log_name = "test_llm_calls.log" if target_dir == TEST_LOGS_DIR else "llm_calls.log"
    llm_log_path = target_dir / llm_log_name

    mode_label = "TEST" if target_dir == TEST_LOGS_DIR else "GENERAL"
    now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    # Format response if Pydantic model or dict
    if hasattr(response_content, "model_dump_json"):
        formatted_output = response_content.model_dump_json(indent=2)
    elif isinstance(response_content, (dict, list)):
        formatted_output = json.dumps(response_content, indent=2)
    else:
        formatted_output = str(response_content).strip()

    total_tokens = prompt_tokens + completion_tokens

    delimiter = "=" * 80
    sub_delimiter = "-" * 80

    entry = (
        f"\n{delimiter}\n"
        f"[{now_str} UTC] [{mode_label}] LLM INVOCATION\n"
        f"Provider:        {provider}\n"
        f"Model:           {model}\n"
        f"API Key:         {api_key_masked or 'N/A'}\n"
        f"Latency:         {latency_ms:.1f} ms\n"
        f"Status:          {status}\n"
        f"Caller:          {caller or 'LLMService'}\n"
        f"Tokens:          Prompt={prompt_tokens} | Completion={completion_tokens} | Total={total_tokens}\n"
        f"Est. Value:      ${estimated_cost:.6f} [{transparency_badge}]\n"
        f"{sub_delimiter}\n"
        f"SYSTEM PROMPT:\n{system_prompt.strip() if system_prompt else 'None'}\n"
        f"{sub_delimiter}\n"
        f"USER PROMPT:\n{user_prompt.strip()}\n"
        f"{sub_delimiter}\n"
        f"RESPONSE OUTPUT:\n{formatted_output}\n"
        f"{delimiter}\n"
    )

    try:
        with open(llm_log_path, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as e:
        logging.getLogger(__name__).warning("Failed to write LLM interaction log: %s", e)
