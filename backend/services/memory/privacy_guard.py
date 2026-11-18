from dataclasses import dataclass
import re
from typing import Optional


@dataclass
class MemoryValidationResult:
    allowed: bool
    sanitized_content: str
    rejection_reason: Optional[str] = None


# Strip XML/prompt-boundary tags so stored memories can never break prompt sandboxing
_PROMPT_BOUNDARY_TAGS = re.compile(
    r"</?(?:retrieved_context|user_preferences_and_context|conversation_history|system|assistant|user|untrusted_web_source|external_web_sources)\b[^>]*>",
    re.IGNORECASE,
)

# Deterministic secret / credential patterns (ALWAYS blocked, even on explicit commands)
_SECRET_PATTERNS = [
    ("API key or token", re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|gsk_[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AIza[0-9A-Za-z_-]{20,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,})\b")),
    ("JWT token", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("credential assignment", re.compile(r"\b(?:password|passwd|pwd|api[_ ]?key|secret[_ ]?key|access[_ ]?token|auth[_ ]?token|bearer\s+token|private[_ ]?key)\s*(?:is|=|:)\s*\S+", re.IGNORECASE)),
    ("database connection credentials", re.compile(r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^/\s:@]+:[^/\s@]+@", re.IGNORECASE)),
    ("payment card number", re.compile(r"\b(?:\d[ -]*?){13,19}\b")),
    ("government ID number", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
]

# Sensitive personal categories blocked from automatic storage and protected by policy
_SENSITIVE_PERSONAL_PATTERNS = [
    (
        "health or medical information",
        re.compile(
            r"\b(?:diagnosed with|diagnosis of|my diagnosis|medical condition|chronic illness|taking (?:medication|antidepressants|insulin|chemotherapy)|prescription for|blood pressure is|diabetes|cancer|depression|anxiety disorder|bipolar|adhd|therapy session|hospitalized)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "financial account details",
        re.compile(
            r"\b(?:bank account(?:\s+number)?|routing number|ifsc|cvv|atm pin|upi pin|debit card|credit card number|salary is|net worth|tax id|ssn|aadhaar|pan number)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "private information about children",
        re.compile(
            r"\b(?:my (?:son|daughter|child|children|kids?)\b.{0,50}\b(?:school|age|born|birthday|medical|allergy|allergies|address|named|name is|goes to|studies at))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "intimate or sexual information",
        re.compile(
            r"\b(?:sexual orientation|sex life|intimate relationship|fetish)\b",
            re.IGNORECASE,
        ),
    ),
]


def sanitize_memory_content(text: str) -> str:
    """Removes prompt-sandboxing tags and normalizes whitespace."""
    cleaned = _PROMPT_BOUNDARY_TAGS.sub("", text or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def detect_secret_or_credential(text: str) -> Optional[str]:
    for label, pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            return label
    return None


def detect_sensitive_personal_info(text: str) -> Optional[str]:
    for label, pattern in _SENSITIVE_PERSONAL_PATTERNS:
        if pattern.search(text):
            return label
    return None


def validate_memory_content(text: str, *, is_explicit: bool = False) -> MemoryValidationResult:
    """
    Validates and sanitizes candidate memory text before persistence.
    - Credentials and authentication secrets are ALWAYS rejected.
    - Sensitive personal categories (health, financial accounts, children, intimate info)
      are rejected by default policy.
    """
    sanitized = sanitize_memory_content(text)
    if len(sanitized) < 4:
        return MemoryValidationResult(
            allowed=False,
            sanitized_content=sanitized,
            rejection_reason="Memory content is too short to be stored.",
        )
    if len(sanitized) > 1000:
        sanitized = sanitized[:1000].rstrip()

    secret_hit = detect_secret_or_credential(sanitized)
    if secret_hit:
        return MemoryValidationResult(
            allowed=False,
            sanitized_content="",
            rejection_reason=(
                f"Cannot store {secret_hit} in memory. For security, Cortex never persists "
                "passwords, API keys, tokens, or authentication credentials."
            ),
        )

    sensitive_hit = detect_sensitive_personal_info(sanitized)
    if sensitive_hit:
        if is_explicit:
            return MemoryValidationResult(
                allowed=False,
                sanitized_content="",
                rejection_reason=(
                    f"Cannot store {sensitive_hit} in long-term memory under Cortex's privacy policy."
                ),
            )
        return MemoryValidationResult(
            allowed=False,
            sanitized_content="",
            rejection_reason=f"Skipped storing sensitive {sensitive_hit}.",
        )

    return MemoryValidationResult(allowed=True, sanitized_content=sanitized)
