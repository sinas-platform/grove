"""Synthesis — claim drafting, evidence binding, faithfulness validation."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CallerIdentity, get_caller, require_permission
from app.db import get_session
from app.models import Answer, AnswerClaim, ClaimEvidence, Result
from app.schemas.runtime import ClaimEvidenceIn, ClaimEvidenceOut, ClaimOut

router = APIRouter(prefix="/synthesis", tags=["synthesis"])


class StartAnswerIn(BaseModel):
    question: str
    source_result_id: uuid.UUID | None = None
    source_dossier_id: uuid.UUID | None = None


@router.post(
    "/answers",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("grove.answers.write:own"))],
)
async def start_answer(
    payload: StartAnswerIn,
    session: AsyncSession = Depends(get_session),
    caller: CallerIdentity = Depends(get_caller),
):
    if payload.source_result_id is None and payload.source_dossier_id is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "must specify source_result_id or source_dossier_id"
        )

    # If a source result is specified, inherit its owner+roles for the answer.
    owner_id, roles = caller.user_id, list(caller.roles or [])
    if payload.source_result_id:
        result = await session.get(Result, payload.source_result_id)
        if result is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "source result not found")
        owner_id = result.owner_id
        roles = list(result.roles or [])

    row = Answer(
        source_result_id=payload.source_result_id,
        source_dossier_id=payload.source_dossier_id,
        question=payload.question,
        owner_id=owner_id,
        roles=roles,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return {"id": row.id, "status": row.status}


class DraftClaimIn(BaseModel):
    sequence: int
    claim_text: str
    claim_type: str | None = None


@router.post(
    "/answers/{answer_id}/claims",
    response_model=ClaimOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("grove.answers.write:own"))],
)
async def draft_claim(
    answer_id: uuid.UUID,
    payload: DraftClaimIn,
    session: AsyncSession = Depends(get_session),
):
    row = AnswerClaim(answer_id=answer_id, **payload.model_dump())
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


@router.post(
    "/claims/{claim_id}/evidence",
    response_model=ClaimEvidenceOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("grove.answers.write:own"))],
)
async def bind_evidence(
    claim_id: uuid.UUID,
    payload: ClaimEvidenceIn,
    session: AsyncSession = Depends(get_session),
):
    row = ClaimEvidence(
        claim_id=claim_id,
        document_id=payload.document_id,
        document_version_id=payload.document_version_id,
        span=payload.span.model_dump(),
        stance=payload.stance,
        relevance=payload.relevance,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


class ValidationVerdict(BaseModel):
    validated: bool
    reasoning: str


@router.post(
    "/evidence/{evidence_id}/verdict",
    dependencies=[Depends(require_permission("grove.answers.write:own"))],
)
async def record_validation_verdict(
    evidence_id: uuid.UUID,
    payload: ValidationVerdict,
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(ClaimEvidence, evidence_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "evidence not found")
    row.validated = payload.validated
    row.validation_reasoning = payload.reasoning
    await session.commit()
    return {"ok": True}


@router.post(
    "/answers/{answer_id}/publish",
    dependencies=[Depends(require_permission("grove.answers.write:own"))],
)
async def publish_answer(
    answer_id: uuid.UUID, session: AsyncSession = Depends(get_session)
):
    row = await session.get(Answer, answer_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "answer not found")

    # Refuse to publish if any claim has unvalidated evidence
    bad = (
        await session.execute(
            select(ClaimEvidence)
            .join(AnswerClaim, AnswerClaim.id == ClaimEvidence.claim_id)
            .where(AnswerClaim.answer_id == answer_id)
            .where(ClaimEvidence.validated.is_(False))
            .limit(1)
        )
    ).scalar_one_or_none()
    if bad is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "answer has unvalidated evidence — run faithfulness validation first",
        )

    row.status = "published"
    row.published_at = datetime.now(timezone.utc)
    await session.commit()
    return {"id": row.id, "status": row.status}
