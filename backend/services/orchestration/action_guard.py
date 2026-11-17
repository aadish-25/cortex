import logging
from typing import Any, Dict, List, Optional
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


class DomainField(BaseModel):
    key: str = Field(description="Parameter name, e.g. amount, device_model, env, start_date")
    value: str = Field(description="Parameter value")


class ActionFeasibilitySchema(BaseModel):
    """
    Strict OpenAI/Groq compliant schema for Fast LLM Operational Feasibility Classification.
    Uses typed DomainField list instead of raw dict to guarantee 'additionalProperties: false' validity.
    """
    is_feasible: bool = Field(
        description=(
            "True if request is a legitimate enterprise workplace operational request "
            "(e.g., IT support, bug fix, laptop requisition, VPN access, HR inquiry, leave booking, "
            "relocation, expense reimbursement, tax slab dispute, corporate travel). "
            "False if frivolous, personal non-work request (e.g., ordering personal food like pizza/pasta/coffee, "
            "planning personal vacations like Goa, jokes, chit-chat, personal advice)."
        )
    )
    refusal_reason: Optional[str] = Field(
        default=None,
        description="Standard refusal string if rejected as frivolous/non-work",
    )
    department: str = Field(
        default="Engineering",
        description="Target department: 'Engineering', 'HR', or 'Finance'",
    )
    taxonomy_tier: TicketTaxonomy = Field(
        default=TicketTaxonomy.SERVICE_REQUEST,
        description="Universal taxonomy tier: INCIDENT, SERVICE_REQUEST, INQUIRY, or DISPUTE",
    )
    title: str = Field(
        default="",
        description="Concise, 1-line professional title for the ticket",
    )
    description: str = Field(
        default="",
        description="Clear, structured operational brief for the ticket",
    )
    priority: TicketPriority = Field(
        default=TicketPriority.MEDIUM,
        description="Urgency: LOW, MEDIUM, HIGH, or CRITICAL",
    )
    domain_fields: List[DomainField] = Field(
        default_factory=list,
        description="Extracted key-value operational parameters",
    )


class ActionFeasibilityResult:
    """Standardized result returned by ActionGuard."""

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


class ActionGuard:
    """
    Action Feasibility Guard (Anti-Frivolous Filter) for Cortex.
    Evaluates operational requests using the Fast LLM Router (Groq Qwen 27B)
    with strict schema enforcement. Rejects non-work queries without rigid regex keyword lists.
    """

    async def evaluate(
        self,
        query: str,
        department_hint: Optional[str] = None,
        taxonomy_hint: Optional[TicketTaxonomy] = None,
    ) -> ActionFeasibilityResult:
        """
        Evaluates whether query is a valid operational action intent via Fast LLM semantic classification.
        """
        system_prompt = (
            "You are Cortex's Action Feasibility Guard and Operational Request Classifier. "
            "Analyze the semantic meaning of the user's prompt to determine if it is an authentic enterprise "
            "workplace operational request or a frivolous non-work personal request.\n\n"
            "FEASIBLE OPERATIONAL REQUESTS:\n"
            "- Engineering & IT: System outages, 500 errors, database locks, pipeline bugs, laptop provisioning, "
            "VPN/bastion access, cloud credentials, monitor/RAM hardware requisitions.\n"
            "- Human Resources: Medical/annual leave booking, maternity/paternity policy inquiries, relocation assistance, "
            "POSH grievances, health insurance benefits.\n"
            "- Finance: Corporate expense claims, rejected travel/meal reimbursements, GST invoice disputes, "
            "tax slab deduction inquiries, vendor invoice status.\n\n"
            "INCOMPATIBLE / FRIVOLOUS REQUESTS (Must be refused):\n"
            "- Personal food/drink ordering (e.g., ordering pizza, pasta, coffee, burgers, drinks).\n"
            "- Personal non-work vacations/trips (e.g., holiday in Goa with friends, booking weekend parties).\n"
            "- Personal life, dating, chit-chat, jokes, songs, traffic complaints, weather.\n\n"
            "RULES:\n"
            "1. If non-work or frivolous: set is_feasible=False, and refusal_reason='"
            + STANDARD_REFUSAL_MESSAGE
            + "'\n"
            "2. If feasible: set is_feasible=True, department ('Engineering', 'HR', or 'Finance'), "
            "taxonomy_tier ('INCIDENT', 'SERVICE_REQUEST', 'INQUIRY', 'DISPUTE'), "
            "write a crisp 1-line title, structured description, priority ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL'), "
            "and extract key-value domain_fields.\n"
            "3. If an expense claim mentions meals or dinners (e.g. 'Reimburse pasta dinner with client'), "
            "this is a legitimate FINANCE DISPUTE/REQUEST, NOT a frivolous food order!"
        )

        try:
            parsed = await llm_service.generate_structured(
                prompt=f"User Request: {query}",
                response_model=ActionFeasibilitySchema,
                system_prompt=system_prompt,
                model=settings.FAST_ROUTING_MODEL,
            )

            if parsed is None:
                raise RuntimeError("LLM structured output returned None.")

            if not parsed.is_feasible:
                return ActionFeasibilityResult(
                    is_feasible=False,
                    refusal_reason=parsed.refusal_reason or STANDARD_REFUSAL_MESSAGE,
                )

            # Convert structured DomainField list back to dict
            domain_dict: Dict[str, Any] = {df.key: df.value for df in parsed.domain_fields}

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
                domain_fields=domain_dict,
            )

        except Exception as e:
            logger.error("Error running ActionGuard Fast LLM evaluation: %s", e)
            # Re-raise or handle failure cleanly without brittle keyword matching
            raise RuntimeError(f"ActionGuard classification failed: {e}") from e


# Global singleton instance
action_guard = ActionGuard()
