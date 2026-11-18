from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from backend.models.memory import UserMemory

EXACT_DUPLICATE_THRESHOLD = 0.88
MEMORY_RETRIEVAL_THRESHOLD = 0.52


@dataclass
class MemoryCommandResult:
    handled: bool = False
    is_pure_memory_command: bool = False
    remaining_user_query: Optional[str] = None
    actions_performed: List[str] = field(default_factory=list)
    saved: List[Dict[str, Any]] = field(default_factory=list)
    updated: List[Dict[str, Any]] = field(default_factory=list)
    already_existed: List[str] = field(default_factory=list)
    deleted: List[Dict[str, Any]] = field(default_factory=list)
    retrieved: List[Dict[str, Any]] = field(default_factory=list)
    rejected: List[Dict[str, str]] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)
    clarification_question: Optional[str] = None
    factual_summary: str = ""

    def has_mutations(self) -> bool:
        return bool(self.saved or self.updated or self.deleted or self.rejected)

    def to_sse_payload(self) -> Optional[Dict[str, Any]]:
        if not self.handled:
            return None
        if not (self.saved or self.updated or self.deleted or self.rejected or self.retrieved):
            return None
        return {
            "actions": list(dict.fromkeys(self.actions_performed)),
            "saved": [{"memory_id": m["memory_id"], "text": m["text"], "category": m["category"]} for m in self.saved],
            "updated": [{"memory_id": m["memory_id"], "text": m["text"], "category": m["category"]} for m in self.updated],
            "deleted_count": len(self.deleted),
            "deleted": [{"memory_id": m["memory_id"], "text": m["text"], "category": m["category"]} for m in self.deleted],
            "retrieved_count": len(self.retrieved),
            "rejected": self.rejected,
        }


def format_existing_memories(memories: List[UserMemory]) -> str:
    if not memories:
        return "(none)"
    return "\n".join(
        f"- [id={m.id}] ({m.category}, updated={m.updated_at.date().isoformat()}) {m.content}"
        for m in memories
    )


def build_factual_summary(result: MemoryCommandResult) -> str:
    sections: List[str] = []

    if result.clarification_question:
        sections.append(result.clarification_question)

    if result.saved:
        bullets = "\n".join(f"- **[{m['category']}]** {m['text']}" for m in result.saved)
        sections.append(f"Saved to your long-term memory:\n{bullets}")

    if result.already_existed:
        bullets = "\n".join(f"- {t}" for t in result.already_existed)
        sections.append(f"Already saved in your long-term memory:\n{bullets}")

    if result.updated:
        lines = []
        for m in result.updated:
            prev = m.get("previous_text")
            if prev:
                lines.append(f"- **[{m['category']}]** Updated from *\"{prev}\"* to **\"{m['text']}\"**")
            else:
                lines.append(f"- **[{m['category']}]** {m['text']}")
        sections.append("Updated your long-term memory:\n" + "\n".join(lines))

    if "DELETE" in result.actions_performed:
        if result.deleted:
            bullets = "\n".join(f"- **[{m['category']}]** {m['text']}" for m in result.deleted)
            sections.append(f"Deleted {len(result.deleted)} memory item(s):\n{bullets}")
        elif not result.clarification_question and not result.failed:
            sections.append("No matching memories were found to delete.")

    if "RETRIEVE" in result.actions_performed:
        if result.retrieved:
            bullets = "\n".join(f"- **[{m['category']}]** {m['text']}" for m in result.retrieved)
            sections.append(f"Here is what I currently remember about you ({len(result.retrieved)} item(s)):\n{bullets}")
        else:
            sections.append("I don't have any matching long-term memories saved for you right now.")

    if result.rejected:
        bullets = "\n".join(f"- {r['reason']}" for r in result.rejected)
        sections.append(f"Memory policy notice:\n{bullets}")

    if result.failed:
        bullets = "\n".join(f"- {err}" for err in result.failed)
        sections.append(f"Memory operation could not be completed:\n{bullets}")

    return "\n\n".join(sections).strip()
