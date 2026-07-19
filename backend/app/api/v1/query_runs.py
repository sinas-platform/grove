"""Query runs — one-call, server-supervised question pipelines.

POST /query-runs        start a run (returns immediately; pipeline runs as a
                        background task — see services/query_runner)
GET  /query-runs        list runs (visibility-scoped)
GET  /query-runs/{id}   status, stage telemetry, artifact ids
POST /query-runs/{id}/resume   re-launch a failed run from its last good stage
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CallerIdentity, get_caller, require_permission
from app.db import get_session
from app.models.query import QueryRun
from app.schemas.common import OwnedOut
from app.services.query_runner import run_pipeline
from app.services.visibility import visible_clause

router = APIRouter(prefix="/query-runs", tags=["query-runs"])

# Keep strong references so tasks aren't garbage-collected mid-run.
_tasks: set[asyncio.Task] = set()


def _launch(run_id: uuid.UUID) -> None:
    task = asyncio.create_task(run_pipeline(run_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


class QueryRunIn(BaseModel):
    question: str = Field(min_length=8)
    mode: str = Field(default="full", pattern="^(full|retrieval|synthesis)$")
    subqueries: list[str] | None = None  # skip decomposition when provided
    # required for mode="synthesis": the published result to answer from
    parent_result_id: uuid.UUID | None = None
    run_discovery: bool = False


class QueryRunOut(OwnedOut):
    question: str
    mode: str = "full"
    status: str
    subqueries: list[str] | None = None
    parent_result_id: uuid.UUID | None = None
    answer_id: uuid.UUID | None = None
    error: str | None = None
    telemetry: dict[str, Any] = {}


@router.post(
    "",
    response_model=QueryRunOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("grove.results.write:own"))],
)
async def create_query_run(
    payload: QueryRunIn,
    session: AsyncSession = Depends(get_session),
    caller: CallerIdentity = Depends(get_caller),
):
    if payload.mode == "synthesis" and payload.parent_result_id is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "mode=synthesis requires parent_result_id"
        )
    run = QueryRun(
        question=payload.question,
        mode=payload.mode,
        subqueries=payload.subqueries,
        parent_result_id=payload.parent_result_id,
        run_discovery=payload.run_discovery,
        owner_id=caller.user_id,
        roles=list(caller.roles or []),
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)
    _launch(run.id)
    return run


@router.get("", response_model=list[QueryRunOut])
async def list_query_runs(
    session: AsyncSession = Depends(get_session),
    caller: CallerIdentity = Depends(get_caller),
    limit: int = 20,
):
    read_all = await caller.has_permission("grove.results.read:all")
    rows = (
        await session.execute(
            select(QueryRun)
            .where(visible_clause(QueryRun, caller, read_all=read_all))
            .order_by(QueryRun.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return rows


async def _visible_run_or_404(
    run_id: uuid.UUID, session: AsyncSession, caller: CallerIdentity
) -> QueryRun:
    read_all = await caller.has_permission("grove.results.read:all")
    row = (
        await session.execute(
            select(QueryRun)
            .where(QueryRun.id == run_id)
            .where(visible_clause(QueryRun, caller, read_all=read_all))
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "query run not found")
    return row


@router.get("/{run_id}", response_model=QueryRunOut)
async def get_query_run(
    run_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    caller: CallerIdentity = Depends(get_caller),
):
    return await _visible_run_or_404(run_id, session, caller)


@router.post(
    "/{run_id}/resume",
    response_model=QueryRunOut,
    dependencies=[Depends(require_permission("grove.results.write:own"))],
)
async def resume_query_run(
    run_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    caller: CallerIdentity = Depends(get_caller),
):
    run = await _visible_run_or_404(run_id, session, caller)
    if run.status not in ("failed",):
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"run is {run.status}; only failed runs resume"
        )
    run.status = "pending"
    run.error = None
    await session.commit()
    await session.refresh(run)
    _launch(run.id)
    return run
