"""Retrieval — introspect, draft results, claim/answer publishing.

The current draft filter is persisted on `Result.filter` and mutated via
the `/retrieval/results/{id}/filter/*` endpoints (see
`app.api.v1.result_filter` and ADR `2026-05-14-stateful-filter-on-result`).
The stateless `/retrieval/introspect` endpoint still exists for ad-hoc
queries (UI, scripts) that don't want to materialize a Result first.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CallerIdentity, get_caller, require_permission
from app.db import get_session
from app.schemas.runtime import (
    GroveFilter,
    IntrospectIn,
    IntrospectOut,
    MatchingDocumentsIn,
    MatchingDocumentsOut,
)
from app.services.introspect import introspect_with_filter, matching_document_ids

router = APIRouter(prefix="/retrieval", tags=["retrieval"])


@router.post("/introspect", response_model=IntrospectOut)
async def introspect(
    payload: IntrospectIn,
    session: AsyncSession = Depends(get_session),
    caller: CallerIdentity = Depends(get_caller),
):
    """Stateless introspect — pass a filter inline. Kept for ad-hoc/UI use.
    The agent loop should prefer `/results/{id}/introspect`, which uses the
    persisted filter and avoids re-emitting it on every iteration."""
    f = payload.filter or GroveFilter()
    return await introspect_with_filter(session, caller, f, payload.fields, payload.top_k)


@router.post("/matching-documents", response_model=MatchingDocumentsOut)
async def matching_documents(
    payload: MatchingDocumentsIn,
    session: AsyncSession = Depends(get_session),
    caller: CallerIdentity = Depends(get_caller),
):
    """Enumerate the document ids matching the filter.

    Companion to `/introspect`: introspect answers HOW MANY documents match and
    their value distribution; this answers WHICH ones, so the agent can add them
    to a Result. Same filter body and query logic, capped by `limit` (default
    50, max 200)."""
    f = payload.filter or GroveFilter()
    ids = await matching_document_ids(session, caller, f, payload.limit)
    return MatchingDocumentsOut(document_ids=ids)


# ───────────────────── draft result mutation operations ─────────────────────
class CreateResultIn(BaseModel):
    query: str
    invoked_skill_names: list[str] | None = None
    parent_result_id: uuid.UUID | None = None


class AddFilesIn(BaseModel):
    document_ids: list[uuid.UUID]
    reason: str | None = None
    added_by_agent: str | None = None


class TraceIn(BaseModel):
    sequence: int
    agent: str
    action: str
    parameters: dict | None = None
    outcome: dict | None = None


@router.post(
    "/results",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("grove.results.write:own"))],
)
async def create_draft_result(
    payload: CreateResultIn,
    session: AsyncSession = Depends(get_session),
    caller: CallerIdentity = Depends(get_caller),
):
    from app.models import Result

    row = Result(
        query=payload.query,
        invoked_skill_names=payload.invoked_skill_names,
        parent_result_id=payload.parent_result_id,
        owner_id=caller.user_id,
        roles=caller.roles or [],
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return {"id": row.id, "status": row.status}


@router.post(
    "/results/{result_id}/files",
    dependencies=[Depends(require_permission("grove.results.write:own"))],
)
async def add_files_to_result(
    result_id: uuid.UUID,
    payload: AddFilesIn,
    session: AsyncSession = Depends(get_session),
):
    from app.models import Result, ResultDocument

    result = await session.get(Result, result_id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "result not found")
    for did in payload.document_ids:
        session.add(
            ResultDocument(
                result_id=result_id,
                document_id=did,
                reason=payload.reason,
                added_by_agent=payload.added_by_agent,
            )
        )
    await session.commit()
    return {"added": len(payload.document_ids)}


@router.post(
    "/results/{result_id}/trace",
    dependencies=[Depends(require_permission("grove.results.write:own"))],
)
async def append_trace(
    result_id: uuid.UUID,
    payload: TraceIn,
    session: AsyncSession = Depends(get_session),
):
    from datetime import datetime, timezone

    from app.models import ResultTrace

    session.add(
        ResultTrace(
            result_id=result_id,
            sequence=payload.sequence,
            agent=payload.agent,
            action=payload.action,
            parameters=payload.parameters,
            outcome=payload.outcome,
            occurred_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()
    return {"ok": True}


@router.post(
    "/results/{result_id}/publish",
    dependencies=[Depends(require_permission("grove.results.write:own"))],
)
async def publish_result(
    result_id: uuid.UUID, session: AsyncSession = Depends(get_session)
):
    from datetime import datetime, timezone

    from sqlalchemy import func, select

    from app.models import Result, ResultDocument

    result = await session.get(Result, result_id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "result not found")
    doc_count = (
        await session.execute(
            select(func.count())
            .select_from(ResultDocument)
            .where(ResultDocument.result_id == result_id)
        )
    ).scalar_one()
    if doc_count == 0:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "cannot publish a result with no documents",
        )
    result.status = "published"
    result.published_at = datetime.now(timezone.utc)
    await session.commit()
    return {"id": result.id, "status": result.status}
