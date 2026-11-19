import logging
import uuid
from typing import Optional
from pathlib import Path
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.security import get_current_user
from backend.db.database import get_db
from backend.models.action_draft import ActionDraftCreateRequest, ActionDraftResponse
from backend.models.user import User
from backend.services.orchestration.action_engine import (
    ActionError,
    ActionInfeasibleError,
    DraftAccessDeniedError,
    DraftExpiredError,
    DraftNotFoundError,
    action_engine,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/actions", tags=["actions"])


class ActionConfirmResponse(BaseModel):
    ticket_id: uuid.UUID
    jira_key: Optional[str] = None
    jira_url: Optional[str] = None
    status: str
    message: Optional[str] = None
    draft: ActionDraftResponse


@router.post("/draft", response_model=ActionDraftResponse, status_code=status.HTTP_201_CREATED)
async def create_action_draft(
    payload: ActionDraftCreateRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """
    Evaluates operational feasibility and prepares an ActionDraft with a strict 15-minute TTL.
    Cleanly refuses frivolous, non-work queries per PRD Section 8.3.
    """
    try:
        draft_response = await action_engine.create_draft(
            session=session,
            user=current_user,
            request=payload,
        )
        return draft_response
    except ActionInfeasibleError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        )
    except Exception as exc:
        logger.error("Failed to create action draft: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred while preparing action draft: {exc}",
        )


@router.get("/{draft_id}", response_model=ActionDraftResponse)
async def get_action_draft(
    draft_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """
    Retrieves an ActionDraft with remaining TTL seconds.
    Enforces multi-tenant user isolation.
    """
    try:
        return await action_engine.get_draft(session, current_user, draft_id)
    except DraftNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except DraftAccessDeniedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))


from backend.models.enums import TicketPriority


class ActionDraftUpdateRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[TicketPriority] = None
    department: Optional[str] = None
    domain_fields: Optional[dict] = None


@router.patch("/{draft_id}", response_model=ActionDraftResponse)
async def update_action_draft(
    draft_id: uuid.UUID,
    payload: ActionDraftUpdateRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """
    Updates editable fields on a pending ActionDraft before user commits it.
    """
    try:
        return await action_engine.update_draft(
            session=session,
            user=current_user,
            draft_id=draft_id,
            title=payload.title,
            description=payload.description,
            priority=payload.priority,
            department=payload.department,
            domain_fields=payload.domain_fields,
        )
    except DraftNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except DraftAccessDeniedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except DraftExpiredError as exc:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=str(exc))
    except ActionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/{draft_id}/confirm", response_model=ActionConfirmResponse)
async def confirm_action_draft(
    draft_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """
    Confirms an ActionDraft (HITL execution):
      - Verifies strict 15-minute TTL (returns 410 GONE if expired)
      - Idempotently prevents double submissions
      - Dispatches ticket to Jira Cloud (JSM)
      - Commits Ticket to PostgreSQL
    """
    try:
        ticket, draft_resp = await action_engine.confirm_draft(
            session=session,
            user=current_user,
            draft_id=draft_id,
        )
        return ActionConfirmResponse(
            ticket_id=ticket.id,
            jira_key=ticket.jira_key,
            jira_url=draft_resp.jira_url,
            status=ticket.status.value if hasattr(ticket.status, "value") else str(ticket.status),
            message=f"Your ticket has been successfully raised. Please note that your ticket ID is {ticket.jira_key}.",
            draft=draft_resp,
        )
    except DraftNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except DraftAccessDeniedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except DraftExpiredError as exc:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=str(exc))
    except RuntimeError as exc:
        logger.error("Upstream Jira dispatch failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Jira integration dispatch failed: {exc}",
        )
    except Exception as exc:
        logger.error("Error confirming action draft: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to confirm action draft: {exc}",
        )


@router.post("/{draft_id}/discard", response_model=ActionDraftResponse)
async def discard_action_draft(
    draft_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """
    Discards a pending ActionDraft.
    """
    try:
        return await action_engine.discard_draft(
            session=session,
            user=current_user,
            draft_id=draft_id,
        )
    except DraftNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except DraftAccessDeniedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ActionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


class AttachmentUploadResponse(BaseModel):
    url: str
    filename: str
    size_bytes: int
    storage_type: str


ALLOWED_ATTACHMENT_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".pdf"}
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024  # 10 MB


@router.post("/attachments", response_model=AttachmentUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_action_attachment(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    """
    Uploads an image or document receipt attached to a Jira ticket draft.
    Files are stored under cortex/jira_tickets/<username>-<YYYYMMDD-HHMMSS>.<ext>
    Cloudinary functions/tools are internal backend infrastructure and not exposed to the agent.
    """
    filename = file.filename or "receipt.png"
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_ATTACHMENT_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type '{ext}'. Allowed: {sorted(ALLOWED_ATTACHMENT_EXTENSIONS)}",
        )

    content = await file.read(MAX_ATTACHMENT_BYTES + 1)
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty.")
    if len(content) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Attachment exceeds 10 MB limit.")

    from backend.services.storage.cloudinary_service import cloudinary_service

    result = cloudinary_service.upload_jira_attachment(
        file_bytes=content,
        username=current_user.username,
        original_filename=filename,
    )

    return AttachmentUploadResponse(
        url=result["url"],
        filename=result["filename"],
        size_bytes=result["size_bytes"],
        storage_type=result["storage_type"],
    )

