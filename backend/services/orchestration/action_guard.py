import logging
import re
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field

from backend.core.config import settings
from backend.models.enums import TicketPriority, TicketTaxonomy
from backend.services.orchestration.llm_service import llm_service

logger = logging.getLogger(__name__)

STANDARD_REFUSAL_MESSAGE = (
    "I cannot raise an official ticket for this request. "
    "Official tickets are restricted to operational incidents, "
    "service requests, policy inquiries, and formal workplace disputes."
)

FRIVOLOUS_PATTERNS = [
    r"\border\b.*?\b(?:pizza|coffee|latte|cappuccino|tea|lunch|dinner|burger|food|drinks?|snack|beer|beverage)\b",
    r"\b(?:coffee|tea|latte|soup|food|drink)\b.*?\b(?:cold|bad|hot|spilled|terrible)\b",
    r"\b(?:traffic|weather|movie\s+tickets?|party|jokes?|memes?|songs?|playlist)\b",
    r"\btell\b.*?\bjoke\b",
    r"\bplay\b.*?\b(?:song|music)\b",
    r"\b(?:date|girlfriend|boyfriend|romance|tinder)\b",
    r"\bfix\b.*?\btraffic\b",
]


def is_obviously_frivolous(query: str) -> bool:
    """
    Checks if a query is clearly a frivolous, non-work operational request.
    Strictly safeguards legitimate corporate expense and reimbursement requests
    (e.g., client lunch/dinner invoices, meal per diems).
    """
    q_lower = query.lower()

    # Legitimate corporate reimbursement/finance phrases are NEVER frivolous
    if any(k in q_lower for k in [
        "reimburse", "reimbursement", "expense", "claim", "invoice",
        "receipt", "gst", "tds", "allowance", "budget", "billing", "per diem",
    ]):
        return False

    return any(re.search(p, q_lower) for p in FRIVOLOUS_PATTERNS)


class ActionFeasibilitySchema(BaseModel):
    is_feasible: bool = Field(description="True if request is a legitimate workplace/enterprise operational request, False if frivolous or non-work")
    refusal_reason: Optional[str] = Field(default=None, description="Explanation if rejected as non-feasible")
    department: str = Field(default="Engineering", description="Department to route ticket: Engineering, HR, Finance, or IT")
    taxonomy_tier: TicketTaxonomy = Field(default=TicketTaxonomy.SERVICE_REQUEST, description="Universal 4-tier taxonomy")
    title: str = Field(default="", description="Concise, 1-line professional title for the ticket")
    description: str = Field(default="", description="Clear, structured operational brief for the ticket")
    priority: TicketPriority = Field(default=TicketPriority.MEDIUM, description="Urgency: LOW, MEDIUM, HIGH, CRITICAL")
    domain_fields: Dict[str, Any] = Field(default_factory=dict, description="Extracted domain parameters like amount, dates, device")


class ActionFeasibilityResult:
    def __init__(
        self,
        is_feasible: bool,
        refusal_reason: Optional[str] = None,
        department: str = "Engineering",
        taxonomy_tier: TicketTaxonomy = TicketTaxonomy.SERVICE_REQUEST,
        title: str = "",
        description: str = "",
        priority: TicketPriority = TicketPriority.MEDIUM,
        domain_fields: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.is_feasible = is_feasible
        self.refusal_reason = refusal_reason
        self.department = department
        self.taxonomy_tier = taxonomy_tier
        self.title = title
        self.description = description
        self.priority = priority
        self.domain_fields = domain_fields or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_feasible": self.is_feasible,
            "refusal_reason": self.refusal_reason,
            "department": self.department,
            "taxonomy_tier": self.taxonomy_tier.value,
            "title": self.title,
            "description": self.description,
            "priority": self.priority.value,
            "domain_fields": self.domain_fields,
        }


def _heuristic_evaluate(query: str) -> ActionFeasibilityResult:
    """Fast deterministic evaluation for offline/fallback mode."""
    q_lower = query.lower()

    # Check frivolous keywords
    if is_obviously_frivolous(query):
        return ActionFeasibilityResult(
            is_feasible=False,
            refusal_reason=STANDARD_REFUSAL_MESSAGE,
        )

    # Detect Department
    if any(k in q_lower for k in ["salary", "expense", "reimbursement", "tax", "gst", "tds", "invoice", "finance", "bill", "claim"]):
        department = "Finance"
    elif any(k in q_lower for k in ["leave", "vacation", "holiday", "posh", "maternity", "paternity", "hr", "relocation", "benefits", "appraisal"]):
        department = "HR"
    else:
        department = "Engineering"

    # Detect Taxonomy Tier
    if any(k in q_lower for k in ["broken", "crash", "bug", "down", "error", "fail", "500", "outage"]):
        taxonomy = TicketTaxonomy.INCIDENT
        priority = TicketPriority.HIGH
    elif any(k in q_lower for k in ["dispute", "appeal", "rejected", "rejection", "grievance"]):
        taxonomy = TicketTaxonomy.DISPUTE
        priority = TicketPriority.MEDIUM
    elif any(k in q_lower for k in ["policy", "how to", "clarification", "question", "inquiry"]):
        taxonomy = TicketTaxonomy.INQUIRY
        priority = TicketPriority.LOW
    else:
        taxonomy = TicketTaxonomy.SERVICE_REQUEST
        priority = TicketPriority.MEDIUM

    # Title generation
    clean_title = query.strip()
    if len(clean_title) > 60:
        clean_title = clean_title[:57] + "..."

    return ActionFeasibilityResult(
        is_feasible=True,
        department=department,
        taxonomy_tier=taxonomy,
        title=clean_title,
        description=query.strip(),
        priority=priority,
    )


class ActionGuard:
    """
    Action Feasibility Guard (Anti-Frivolous Filter) for Cortex.
    Evaluates operational requests before generating ActionDrafts to prevent
    ticketing spam or non-work ticket creation.
    """

    async def evaluate(
        self,
        query: str,
        department_hint: Optional[str] = None,
        taxonomy_hint: Optional[TicketTaxonomy] = None,
    ) -> ActionFeasibilityResult:
        """
        Evaluates whether query is a valid operational action intent.
        Uses Fast LLM structured classifier cascading with heuristic fallback.
        """
        # Fast regex check first
        if is_obviously_frivolous(query):
            return ActionFeasibilityResult(
                is_feasible=False,
                refusal_reason=STANDARD_REFUSAL_MESSAGE,
            )

        # In test environments without explicit live LLM flag, use fast deterministic evaluation
        from backend.core.logging_config import is_test_environment
        import os
        if is_test_environment() and not os.environ.get("CORTEX_FORCE_LLM_EVAL"):
            res = _heuristic_evaluate(query)
            if department_hint:
                res.department = department_hint
            if taxonomy_hint:
                res.taxonomy_tier = taxonomy_hint
            return res

        if not llm_service.is_configured():
            return _heuristic_evaluate(query)

        system_prompt = (
            "You are Cortex's Action Feasibility Guard. "
            "Evaluate if the user's request is a legitimate enterprise workplace operational request "
            "(e.g., IT support, bug fix, laptop requisition, VPN access, HR inquiry, leave booking, "
            "relocation, expense reimbursement, tax slab dispute) "
            "OR a frivolous non-work request (e.g., ordering pizza, cold coffee, personal chat, memes, jokes).\n\n"
            "Rules:\n"
            "1. If non-work or frivolous: set is_feasible=False, and refusal_reason='"
            + STANDARD_REFUSAL_MESSAGE
            + "'\n"
            "2. If feasible: set is_feasible=True, select department ('Engineering', 'HR', or 'Finance'), "
            "taxonomy_tier ('INCIDENT', 'SERVICE_REQUEST', 'INQUIRY', 'DISPUTE'), "
            "write a crisp 1-line professional title, write a structured operational description, "
            "assign priority ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL'), "
            "and extract any domain fields (e.g., amount, device, dates) as key-value pairs."
        )

        try:
            parsed = await llm_service.generate_structured(
                prompt=f"User Request: {query}",
                response_model=ActionFeasibilitySchema,
                system_prompt=system_prompt,
                model=settings.FAST_ROUTING_MODEL,
            )
            if parsed is None:
                return _heuristic_evaluate(query)

            if not parsed.is_feasible:
                return ActionFeasibilityResult(
                    is_feasible=False,
                    refusal_reason=parsed.refusal_reason or STANDARD_REFUSAL_MESSAGE,
                )

            # Apply department or taxonomy hints if explicitly provided by caller
            final_dept = department_hint or parsed.department or "Engineering"
            final_taxonomy = taxonomy_hint or parsed.taxonomy_tier or TicketTaxonomy.SERVICE_REQUEST

            return ActionFeasibilityResult(
                is_feasible=True,
                department=final_dept,
                taxonomy_tier=final_taxonomy,
                title=parsed.title or query[:60],
                description=parsed.description or query,
                priority=parsed.priority or TicketPriority.MEDIUM,
                domain_fields=parsed.domain_fields,
            )

        except Exception as e:
            logger.warning("Error running ActionGuard LLM evaluation (%s); using heuristic fallback", e)
            return _heuristic_evaluate(query)


# Global singleton instance
action_guard = ActionGuard()
