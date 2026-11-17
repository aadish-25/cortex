from datetime import datetime, timedelta, timezone
import json
import logging
from typing import Any, Dict, Optional, Tuple
import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.models.action_draft import (
    ActionDraft,
    ActionDraftCreateRequest,
    ActionDraftPayload,
    ActionDraftResponse,
)
from backend.models.audit import AuditLog
from backend.models.enums import (
    ActionDraftStatus,
    AuditCategory,
    Role,
    TicketPriority,
    TicketStatus,
    TicketTaxonomy,
)
from backend.models.ticket import Ticket
from backend.models.user import User
from backend.services.integrations.jira_service import jira_service
from backend.services.orchestration.action_guard import action_guard

logger = logging.getLogger(__name__)

# Strict 15-minute TTL per PRD Section 7.1
ACTION_DRAFT_TTL_MINUTES = 15


class ActionError(Exception):
    """Base exception for action engine errors."""
    pass


class ActionInfeasibleError(ActionError):
    """Raised when request fails feasibility guard (anti-frivolous filter)."""
    pass


class DraftExpiredError(ActionError):
    """Raised when action draft has exceeded its 15-minute TTL."""
    pass


class DraftNotFoundError(ActionError):
    """Raised when draft cannot be found."""
    pass


class DraftAccessDeniedError(ActionError):
    """Raised when user attempts to access or modify another tenant's draft."""
    pass


class ActionEngine:
    """
    15-Minute ActionDraft State Machine & Human-in-the-Loop Ticketing Engine.
    Coordinates:
      1. Action feasibility evaluation (Anti-frivolous filter)
      2. 15-minute expiring ActionDraft preparation
      3. Idempotent confirmation with live Jira Cloud dispatch
      4. PostgreSQL Ticket record persistence and status transitions
    """

    def _calculate_ttl_remaining(self, expires_at: datetime) -> int:
        now = datetime.now(timezone.utc)
        # Handle timezone-naive dates safely
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        remaining = int((expires_at - now).total_seconds())
        return max(0, remaining)

    def _to_response(self, draft: ActionDraft) -> ActionDraftResponse:
        payload = draft.parsed_payload
        ttl = self._calculate_ttl_remaining(draft.expires_at)
        status = payload.status

        # If time has elapsed and status is still PENDING, mark as EXPIRED
        if ttl == 0 and status == ActionDraftStatus.PENDING:
            status = ActionDraftStatus.EXPIRED

        return ActionDraftResponse(
            draft_id=draft.id,
            action_type=draft.action_type,
            user_id=draft.user_id,
            department=payload.department,
            project_key=payload.project_key,
            taxonomy_tier=payload.taxonomy_tier,
            title=payload.title,
            description=payload.description,
            priority=payload.priority,
            domain_fields=payload.domain_fields,
            status=status,
            is_committed=draft.is_committed,
            expires_at=draft.expires_at,
            created_at=draft.created_at,
            ttl_seconds_remaining=ttl,
            jira_key=payload.jira_key,
            jira_url=payload.jira_url,
        )

    async def create_draft(
        self,
        session: AsyncSession,
        user: User,
        request: ActionDraftCreateRequest,
    ) -> ActionDraftResponse:
        """
        Evaluates operational feasibility and creates a 15-minute expiring ActionDraft.
        """
        guard_result = await action_guard.evaluate(
            query=request.query,
            department_hint=request.department,
            taxonomy_hint=request.taxonomy_tier,
        )

        if not guard_result.is_feasible:
            logger.info("ActionDraft rejected by ActionGuard for user %s: %s", user.email, guard_result.refusal_reason)
            raise ActionInfeasibleError(guard_result.refusal_reason)

        target_project = jira_service.resolve_project_key(guard_result.department)
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=ACTION_DRAFT_TTL_MINUTES)

        # Merge domain fields
        merged_domain = dict(guard_result.domain_fields)
        if request.domain_fields:
            merged_domain.update(request.domain_fields)

        final_priority = request.priority or guard_result.priority or TicketPriority.MEDIUM
        final_taxonomy = request.taxonomy_tier or guard_result.taxonomy_tier or TicketTaxonomy.SERVICE_REQUEST

        payload = ActionDraftPayload(
            department=guard_result.department,
            project_key=target_project,
            taxonomy_tier=final_taxonomy,
            title=guard_result.title,
            description=guard_result.description,
            priority=final_priority,
            domain_fields=merged_domain,
            status=ActionDraftStatus.PENDING,
        )

        draft = ActionDraft(
            user_id=user.id,
            action_type="RAISE_TICKET",
            payload=payload.model_dump_json(),
            expires_at=expires_at,
            is_committed=False,
            created_at=now,
        )

        session.add(draft)
        await session.commit()
        await session.refresh(draft)

        logger.info(
            "Created ActionDraft %s for user %s (Project: %s, TTL: 15m)",
            draft.id,
            user.email,
            target_project,
        )

        return self._to_response(draft)

    async def get_draft(
        self,
        session: AsyncSession,
        user: User,
        draft_id: uuid.UUID,
    ) -> ActionDraftResponse:
        """Retrieves an ActionDraft verifying tenant boundary."""
        stmt = select(ActionDraft).where(ActionDraft.id == draft_id)
        result = await session.execute(stmt)
        draft = result.scalar_one_or_none()

        if not draft:
            raise DraftNotFoundError(f"Action draft {draft_id} not found.")

        # Multi-tenant boundary check: Admin can inspect all, standard users only their own
        if draft.user_id != user.id and user.role != Role.ADMIN:
            raise DraftAccessDeniedError("Access denied: You cannot view action drafts created by another user.")

        return self._to_response(draft)

    async def confirm_draft(
        self,
        session: AsyncSession,
        user: User,
        draft_id: uuid.UUID,
    ) -> Tuple[Ticket, ActionDraftResponse]:
        """
        Confirms an ActionDraft:
          - Enforces strict 15-minute TTL (throws DraftExpiredError if expired)
          - Enforces idempotency (if already committed, returns existing ticket)
          - Creates ticket in live Jira Cloud
          - Persists Ticket in PostgreSQL and updates ActionDraft status to CONFIRMED
        """
        stmt = select(ActionDraft).where(ActionDraft.id == draft_id)
        result = await session.execute(stmt)
        draft = result.scalar_one_or_none()

        if not draft:
            raise DraftNotFoundError(f"Action draft {draft_id} not found.")

        if draft.user_id != user.id and user.role != Role.ADMIN:
            raise DraftAccessDeniedError("Access denied: You cannot confirm an action draft belonging to another user.")

        # 1. Idempotency Check: if already committed, return the existing ticket
        if draft.is_committed:
            ticket_stmt = select(Ticket).where(Ticket.draft_id == draft.id)
            ticket_res = await session.execute(ticket_stmt)
            existing_ticket = ticket_res.scalar_one_or_none()
            if existing_ticket:
                logger.info("Idempotent confirm: Draft %s already committed as Ticket %s", draft.id, existing_ticket.id)
                return existing_ticket, self._to_response(draft)

        # 2. 15-Minute Expiration Check
        now = datetime.now(timezone.utc)
        expires_at = draft.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        if now > expires_at:
            payload = draft.parsed_payload
            payload.status = ActionDraftStatus.EXPIRED
            draft.set_payload(payload)
            session.add(draft)
            await session.commit()
            logger.warning("Draft %s expired at %s (current time: %s)", draft.id, expires_at, now)
            raise DraftExpiredError(
                f"Action draft {draft_id} has expired (TTL 15 minutes exceeded). Please create a new draft."
            )

        # 3. Create Ticket in Jira Cloud
        payload = draft.parsed_payload
        try:
            jira_res = await jira_service.create_issue(
                department=payload.department,
                title=payload.title,
                description=payload.description,
                requester_name=user.name,
                requester_email=user.email,
                taxonomy_tier=payload.taxonomy_tier,
                priority=payload.priority,
                domain_fields=payload.domain_fields,
                project_key_override=payload.project_key,
            )
            jira_key = jira_res.get("key")
            jira_url = jira_res.get("url")
            ticket_status = TicketStatus.CREATED
        except Exception as e:
            logger.error("Jira issue creation failed for draft %s: %s", draft.id, e)
            jira_key = None
            jira_url = None
            ticket_status = TicketStatus.FAILED
            # Re-raise so caller is informed of the failure
            raise RuntimeError(f"Failed to create Jira ticket: {e}") from e

        # 4. Commit Ticket in PostgreSQL
        ticket = Ticket(
            user_id=user.id,
            draft_id=draft.id,
            title=payload.title,
            description=payload.description,
            department=payload.department,
            status=ticket_status,
            jira_key=jira_key,
            created_at=now,
        )
        session.add(ticket)

        # 5. Mark Draft Committed
        draft.is_committed = True
        payload.status = ActionDraftStatus.CONFIRMED
        payload.jira_key = jira_key
        payload.jira_url = jira_url
        draft.set_payload(payload)
        session.add(draft)

        # 6. Audit Trail Logging
        audit_log = AuditLog(
            user_id=user.id,
            event_tag="ACTION_DRAFT_CONFIRMED",
            category=AuditCategory.WORKFLOW,
            status_code=201,
            details=json.dumps({
                "draft_id": str(draft.id),
                "ticket_id": str(ticket.id),
                "jira_key": jira_key,
                "project_key": payload.project_key,
                "department": payload.department,
            }),
            created_at=now,
        )
        session.add(audit_log)

        await session.commit()
        await session.refresh(ticket)
        await session.refresh(draft)

        logger.info(
            "Confirmed ActionDraft %s -> Ticket %s (Jira: %s)",
            draft.id,
            ticket.id,
            jira_key,
        )

        return ticket, self._to_response(draft)

    async def discard_draft(
        self,
        session: AsyncSession,
        user: User,
        draft_id: uuid.UUID,
    ) -> ActionDraftResponse:
        """Discards a pending ActionDraft."""
        stmt = select(ActionDraft).where(ActionDraft.id == draft_id)
        result = await session.execute(stmt)
        draft = result.scalar_one_or_none()

        if not draft:
            raise DraftNotFoundError(f"Action draft {draft_id} not found.")

        if draft.user_id != user.id and user.role != Role.ADMIN:
            raise DraftAccessDeniedError("Access denied: You cannot discard an action draft belonging to another user.")

        if draft.is_committed:
            raise ActionError("Cannot discard an action draft that has already been confirmed and committed.")

        payload = draft.parsed_payload
        payload.status = ActionDraftStatus.DISCARDED
        draft.set_payload(payload)
        session.add(draft)

        # Audit Trail Logging
        audit_log = AuditLog(
            user_id=user.id,
            event_tag="ACTION_DRAFT_DISCARDED",
            category=AuditCategory.WORKFLOW,
            status_code=200,
            details=json.dumps({"draft_id": str(draft.id)}),
            created_at=datetime.now(timezone.utc),
        )
        session.add(audit_log)

        await session.commit()
        await session.refresh(draft)

        logger.info("Discarded ActionDraft %s by user %s", draft.id, user.email)
        return self._to_response(draft)


# Global singleton instance
action_engine = ActionEngine()
