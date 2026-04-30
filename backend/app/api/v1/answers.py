"""Answer reads."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CallerIdentity, get_caller
from app.db import get_session
from app.models import Answer, AnswerClaim, ClaimEvidence
from app.schemas.runtime import AnswerOut, ClaimEvidenceOut, ClaimOut
from app.services.visibility import visible_clause

router = APIRouter(prefix="/answers", tags=["answers"])


@router.get("", response_model=list[AnswerOut])
async def list_answers(
    session: AsyncSession = Depends(get_session),
    caller: CallerIdentity = Depends(get_caller),
    limit: int = 50,
    offset: int = 0,
):
    read_all = await caller.has_permission("grove.answers.read:all")
    stmt = (
        select(Answer)
        .where(visible_clause(Answer, caller, read_all=read_all))
        .order_by(Answer.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return (await session.execute(stmt)).scalars().all()


@router.get("/{answer_id}", response_model=AnswerOut)
async def get_answer(
    answer_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    caller: CallerIdentity = Depends(get_caller),
):
    read_all = await caller.has_permission("grove.answers.read:all")
    stmt = (
        select(Answer)
        .where(Answer.id == answer_id)
        .where(visible_clause(Answer, caller, read_all=read_all))
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "answer not found")
    return row


@router.get("/{answer_id}/claims", response_model=list[ClaimOut])
async def get_answer_claims(answer_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    rows = (
        await session.execute(
            select(AnswerClaim)
            .where(AnswerClaim.answer_id == answer_id)
            .order_by(AnswerClaim.sequence)
        )
    ).scalars().all()
    return rows


@router.get("/claims/{claim_id}/evidence", response_model=list[ClaimEvidenceOut])
async def get_claim_evidence(claim_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    rows = (
        await session.execute(
            select(ClaimEvidence).where(ClaimEvidence.claim_id == claim_id)
        )
    ).scalars().all()
    return rows
