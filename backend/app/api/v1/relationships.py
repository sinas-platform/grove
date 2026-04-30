"""Relationships — read views and proposal review."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CallerIdentity, get_caller, require_permission
from app.db import get_session
from app.models import Relationship, RelationshipProposal
from app.schemas.runtime import RelationshipOut, RelationshipProposalOut

router = APIRouter(prefix="/relationships", tags=["relationships"])


@router.get("", response_model=list[RelationshipOut])
async def list_relationships(
    relationship_definition_id: uuid.UUID | None = None,
    source_id: uuid.UUID | None = None,
    target_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Relationship)
    if relationship_definition_id is not None:
        stmt = stmt.where(Relationship.relationship_definition_id == relationship_definition_id)
    if source_id is not None:
        stmt = stmt.where(Relationship.source_id == source_id)
    if target_id is not None:
        stmt = stmt.where(Relationship.target_id == target_id)
    return (await session.execute(stmt)).scalars().all()


@router.get("/proposals", response_model=list[RelationshipProposalOut])
async def list_proposals(
    status_filter: str = "pending",
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.execute(
            select(RelationshipProposal)
            .where(RelationshipProposal.status == status_filter)
            .order_by(RelationshipProposal.created_at.desc())
        )
    ).scalars().all()
    return rows


class ProposalDecision(BaseModel):
    approve: bool


@router.post(
    "/proposals/{proposal_id}/decision",
    dependencies=[Depends(require_permission("grove.admin:all"))],
)
async def decide_proposal(
    proposal_id: uuid.UUID,
    payload: ProposalDecision,
    session: AsyncSession = Depends(get_session),
    caller: CallerIdentity = Depends(get_caller),
):
    proposal = await session.get(RelationshipProposal, proposal_id)
    if proposal is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "proposal not found")
    proposal.status = "approved" if payload.approve else "rejected"
    proposal.reviewed_at = datetime.now(timezone.utc)
    proposal.reviewed_by = caller.user_id

    if payload.approve:
        rel = Relationship(
            relationship_definition_id=proposal.relationship_definition_id,
            source_id=proposal.source_id,
            target_id=proposal.target_id,
            current_state_id=proposal.suggested_state_id,
            evidence_document_id=proposal.evidence_document_id,
            evidence_span=proposal.evidence_span,
            confidence=proposal.confidence,
            last_verified_at=datetime.now(timezone.utc),
        )
        session.add(rel)

    await session.commit()
    return {"status": proposal.status}
