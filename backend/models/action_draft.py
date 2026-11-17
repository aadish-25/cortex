import json
from datetime import datetime, timezone
from typing import Any, ClassVar, Dict, Optional
import uuid
from sqlmodel import Field, SQLModel

from backend.models.enums import ActionDraftStatus, TicketPriority, TicketTaxonomy


class ActionDraftPayload(SQLModel):
    department: str
    project_key: str
    taxonomy_tier: TicketTaxonomy = TicketTaxonomy.SERVICE_REQUEST
    title: str
    description: str
    priority: TicketPriority = TicketPriority.MEDIUM
    domain_fields: Dict[str, Any] = Field(default_factory=dict)
    status: ActionDraftStatus = ActionDraftStatus.PENDING
    jira_key: Optional[str] = None
    jira_url: Optional[str] = None
    feasibility_reason: Optional[str] = None


class ActionDraft(SQLModel, table=True):
    __tablename__: ClassVar[str] = "action_drafts"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True)
    action_type: str = Field(default="RAISE_TICKET", description="Action name, e.g. 'RAISE_TICKET'")
    payload: str = Field(description="JSON serialized payload of action details")
    expires_at: datetime = Field(description="Expiration timestamp, strictly 15 minutes from creation")
    is_committed: bool = Field(default=False, description="Idempotent flag: set to True when executed")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def parsed_payload(self) -> ActionDraftPayload:
        try:
            data = json.loads(self.payload)
            return ActionDraftPayload(**data)
        except Exception:
            return ActionDraftPayload(
                department="General",
                project_key="KAN",
                title=self.action_type,
                description="",
            )

    def set_payload(self, p: ActionDraftPayload) -> None:
        self.payload = p.model_dump_json()


class ActionDraftCreateRequest(SQLModel):
    query: str
    department: Optional[str] = None
    taxonomy_tier: Optional[TicketTaxonomy] = None
    priority: Optional[TicketPriority] = None
    domain_fields: Optional[Dict[str, Any]] = None


class ActionDraftResponse(SQLModel):
    draft_id: uuid.UUID
    action_type: str
    user_id: uuid.UUID
    department: str
    project_key: str
    taxonomy_tier: TicketTaxonomy
    title: str
    description: str
    priority: TicketPriority
    domain_fields: Dict[str, Any]
    status: ActionDraftStatus
    is_committed: bool
    expires_at: datetime
    created_at: datetime
    ttl_seconds_remaining: int
    jira_key: Optional[str] = None
    jira_url: Optional[str] = None
