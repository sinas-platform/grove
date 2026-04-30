"""Retrieval — introspect, filter mutations, draft results.

The filter object lives in a Sinas State (keyed by chat id), but the agent
sends it back to Grove on every call, so the API is stateless from Grove's
perspective. Introspect and search both apply the same visibility filter.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CallerIdentity, get_caller, require_permission
from app.db import get_session
from app.models import Document, DocumentClassProperty, PropertyValue
from app.schemas.runtime import (
    GroveFilter,
    IntrospectFieldDistribution,
    IntrospectIn,
    IntrospectOut,
)
from app.services.visibility import visible_clause

router = APIRouter(prefix="/retrieval", tags=["retrieval"])


def _apply_filter(stmt, f: GroveFilter):
    if f.document_class_id is not None:
        stmt = stmt.where(Document.document_class_id == f.document_class_id)
    if f.explicit_excludes:
        stmt = stmt.where(~Document.id.in_(f.explicit_excludes))
    if f.text_search:
        # use the FTS index
        from sqlalchemy import text

        stmt = stmt.where(
            Document.id.in_(
                select(text("document_version.document_id"))
                .select_from(text("document_version"))
                .where(text("content_tsvector @@ plainto_tsquery(:q)"))
                .params(q=f.text_search)
            )
        )
    # field_filters and regex_filters are intentionally not implemented in v1
    # against properties — they require joining property_value rows, which is
    # done in the introspect path. Real filter execution is the dev team's
    # task during Phase 3 (see ARCHITECTURE.md §13).
    return stmt


@router.post("/introspect", response_model=IntrospectOut)
async def introspect(
    payload: IntrospectIn,
    session: AsyncSession = Depends(get_session),
    caller: CallerIdentity = Depends(get_caller),
):
    f = payload.filter or GroveFilter()
    read_all = await caller.has_permission("grove.documents.read:all")

    base = select(Document.id).where(visible_clause(Document, caller, read_all=read_all))
    base = _apply_filter(base, f)
    total = (
        await session.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()

    distributions: list[IntrospectFieldDistribution] = []
    if f.document_class_id is not None:
        # All properties on the class — present per-property value counts.
        properties = (
            await session.execute(
                select(DocumentClassProperty).where(
                    DocumentClassProperty.document_class_id == f.document_class_id
                )
            )
        ).scalars().all()
        for prop in properties:
            if payload.fields and prop.name not in payload.fields:
                continue
            counts = (
                await session.execute(
                    select(PropertyValue.value, func.count().label("n"))
                    .where(PropertyValue.property_id == prop.id)
                    .where(PropertyValue.document_id.in_(base))
                    .group_by(PropertyValue.value)
                    .order_by(func.count().desc())
                    .limit(payload.top_k)
                )
            ).all()
            distributions.append(
                IntrospectFieldDistribution(
                    field=prop.name,
                    values=[{"value": c[0], "count": int(c[1])} for c in counts],
                    total_documents=total,
                )
            )

    return IntrospectOut(candidate_count=total, distributions=distributions)


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

    from app.models import Result

    result = await session.get(Result, result_id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "result not found")
    result.status = "published"
    result.published_at = datetime.now(timezone.utc)
    await session.commit()
    return {"id": result.id, "status": result.status}
