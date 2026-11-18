from dataclasses import dataclass, field
import re
from typing import List


@dataclass
class ExplicitCommandSignal:
    is_explicit: bool
    hinted_ops: List[str] = field(default_factory=list)


_EXPLICIT_ADD_PATTERNS = [
    re.compile(r"\bremember\s+(?:that|this|me)\b", re.IGNORECASE),
    re.compile(r"\badd+\s+(?:this|that|it|.+?)?\s*to\s+(?:my\s+)?(?:long[- ]term\s+)?memo?r(?:y|ies)\b", re.IGNORECASE),
    re.compile(r"\bsave\s+(?:this|that|it|.+?)?\s*(?:to|in|as)\s+(?:my\s+|a\s+)?(?:long[- ]term\s+)?(?:memo?r(?:y|ies)|preference)\b", re.IGNORECASE),
    re.compile(r"\bnote\s+that\s+i\b", re.IGNORECASE),
    re.compile(r"\bkeep\s+in\s+mind\s+(?:that\s+)?i\b", re.IGNORECASE),
    re.compile(r"\bstore\s+(?:this|that)\s+in\s+(?:my\s+)?memo?r(?:y|ies)\b", re.IGNORECASE),
]

_EXPLICIT_UPDATE_PATTERNS = [
    re.compile(r"\b(?:update|change|modify|correct|edit)\s+(?:my|that|the)\s+(?:saved\s+)?(?:long[- ]term\s+)?memo?r(?:y|ies)\b", re.IGNORECASE),
    re.compile(r"\b(?:update|change)\s+(?:my\s+)?.{1,50}\bpreference\s+to\b", re.IGNORECASE),
    re.compile(r"\bi\s+no\s+longer\s+(?:prefer|use|want|like|work\s+on)\b", re.IGNORECASE),
    re.compile(r"\bupdate\s+that\s+memo?r(?:y|ies)\b", re.IGNORECASE),
    re.compile(r"\bi(?:'ve|\s+have)?\s+(?:switched|moved)\s+(?:from\s+\S+\s+)?to\s+\S+", re.IGNORECASE),
]

_EXPLICIT_DELETE_PATTERNS = [
    re.compile(r"\bforget\s+(?:that|all|my|everything|about|any)\b", re.IGNORECASE),
    re.compile(r"\b(?:delete|remove|erase|wipe|clear)\s+(?:all\s+)?(?:of\s+)?(?:my\s+|the\s+|everything\s+(?:in\s+|about\s+)?(?:my\s+)?)?(?:saved\s+|long[- ]term\s+)?memo?r(?:y|ies)\b", re.IGNORECASE),
    re.compile(r"\b(?:delete|remove|forget)\s+everything\s+(?:you\s+remember\s+)?about\b", re.IGNORECASE),
    re.compile(r"\b(?:delete|remove|forget)\s+all\s+(?:my\s+)?(?:long[- ]term\s+)?(?:memo?r(?:y|ies)|preferences)\b", re.IGNORECASE),
    re.compile(r"\bforget\s+this\b", re.IGNORECASE),
]

_EXPLICIT_RETRIEVE_PATTERNS = [
    re.compile(r"\bwhat\s+(?:do\s+you|have\s+you)\s+remember(?:ed)?\s+(?:about\s+me)?\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+(?:projects|preferences|facts|things|info(?:rmation)?)\s+(?:do\s+you|have\s+you)\s+(?:remember(?:ed)?|saved|stored)\b", re.IGNORECASE),
    re.compile(r"\b(?:show|list|view|display)\s+(?:me\s+)?(?:everything|all|my)\s+(?:stored\s+in\s+|saved\s+|in\s+)?(?:my\s+)?(?:long[- ]term\s+)?(?:memo?r(?:y|ies)|(?:coding\s+|saved\s+|project[- ]related\s+)?(?:preferences|memo?r(?:y|ies)))\b", re.IGNORECASE),
    re.compile(r"\bwhat(?:'s|\s+is)\s+(?:stored\s+|saved\s+)?in\s+my\s+(?:long[- ]term\s+)?memo?r(?:y|ies)\b", re.IGNORECASE),
]

# Trivial conversational messages that never need memory lookup or extraction
_TRIVIAL_MESSAGES = {
    "hi", "hello", "hey", "yo", "sup", "ok", "okay", "k", "kk", "cool", "nice",
    "thanks", "thank you", "thx", "ty", "bye", "goodbye", "good morning",
    "good afternoon", "good evening", "good night", "yes", "no", "yep", "nope",
    "sure", "got it", "understood", "great", "awesome",
}

# Broad personalization cues where semantic vector overlap alone might be low
_BROAD_PERSONAL_CUES = re.compile(
    r"\b(?:my\s+background|my\s+experience|previous\s+experience|current\s+projects?|"
    r"continue\s+the\s+project|from\s+last\s+month|same\s+stack\s+as\s+before|"
    r"goals\s+we\s+discussed|suit\s+my|based\s+on\s+(?:what\s+you\s+know|my)|"
    r"for\s+my\s+projects?|for\s+my\s+stack|what\s+stack\s+do\s+i|tailored\s+to\s+me)\b",
    re.IGNORECASE,
)

# Cheap heuristic to skip background memory extraction on pure corporate/factual questions
_PERSONAL_SIGNAL_FOR_EXTRACTION = re.compile(
    r"\b(?:i\b|i'm|i've|i'd|i'll|my\b|mine\b|myself\b|prefer|preference|usually|always|"
    r"working\s+on|building|learning|preparing\s+for|switched|moved\s+to|using|"
    r"project|goal|career|mentor|office|based\s+in|located\s+in|stack|workflow)\b",
    re.IGNORECASE,
)


def detect_explicit_memory_command(message: str) -> ExplicitCommandSignal:
    """
    Deterministic, zero-latency pre-filter for explicit user-directed memory commands.
    Returns which operations (ADD / UPDATE / DELETE / RETRIEVE) were signaled.
    """
    if not message or not message.strip():
        return ExplicitCommandSignal(is_explicit=False)

    text = message.strip()
    ops: List[str] = []

    if any(p.search(text) for p in _EXPLICIT_DELETE_PATTERNS):
        ops.append("DELETE")
    if any(p.search(text) for p in _EXPLICIT_UPDATE_PATTERNS):
        ops.append("UPDATE")
    if any(p.search(text) for p in _EXPLICIT_ADD_PATTERNS):
        ops.append("ADD")
    if any(p.search(text) for p in _EXPLICIT_RETRIEVE_PATTERNS):
        ops.append("RETRIEVE")

    return ExplicitCommandSignal(is_explicit=bool(ops), hinted_ops=ops)


def is_trivial_message(message: str) -> bool:
    if not message:
        return True
    cleaned = re.sub(r"[!?. ,]+$", "", message.strip().lower())
    if not cleaned:
        return True
    return cleaned in _TRIVIAL_MESSAGES


def has_broad_personal_cue(message: str) -> bool:
    if not message:
        return False
    return bool(_BROAD_PERSONAL_CUES.search(message))


def should_attempt_background_extraction(message: str) -> bool:
    """
    Fast pre-filter before spawning background extraction LLM work.
    Avoids burning LLM calls on greetings, acknowledgments, or pure third-party questions.
    """
    if is_trivial_message(message):
        return False
    text = message.strip()
    if len(text) < 12:
        return False
    # Pure question with no personal/project/preference cue does not contain durable user facts
    if text.endswith("?") and not _PERSONAL_SIGNAL_FOR_EXTRACTION.search(text):
        return False
    return bool(_PERSONAL_SIGNAL_FOR_EXTRACTION.search(text))
