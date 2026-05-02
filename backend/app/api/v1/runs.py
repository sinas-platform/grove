"""Ingestion runs — bulk reprocessing endpoints + per-doc reprocess sugar."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CallerIdentity, get_caller, require_permission
from app.db import get_session
from app.models import IngestionRun, IngestionRunUnit
from app.schemas.ingestion import (
    RunCreateIn,
    RunCreateOut,
    RunFilter,
    RunOut,
    RunUnitOut,
    Stage,
)
from app.services.ingestion_runner import (
    STAGES,
    expand_filter,
    materialize_run,
    start_worker,
)

router = APIRouter(prefix="/ingestion", tags=["ingestion-runs"])


class StageDescOut(BaseModel):
    key: str
    label: str


@router.get("/stages", response_model=list[StageDescOut])
async def list_stages() -> list[StageDescOut]:
    return [StageDescOut(key=k, label=v["label"]) for k, v in STAGES.items()]


@router.post(
    "/runs",
    response_model=RunCreateOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("grove.admin:all"))],
)
async def create_run(
    payload: RunCreateIn,
    session: AsyncSession = Depends(get_session),
    caller: CallerIdentity = Depends(get_caller),
):
    doc_ids = await expand_filter(session, payload.filter)
    unit_count = len(doc_ids) * len(payload.stages)

    if payload.dry_run:
        return RunCreateOut(
            run_id=None,
            document_count=len(doc_ids),
            unit_count=unit_count,
            status="would_start",
        )

    if not doc_ids:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "filter selected zero documents — refusing to create an empty run",
        )

    run = IngestionRun(
        status="pending",
        stages=list(payload.stages),
        filter=payload.filter.model_dump(mode="json"),
        total_units=unit_count,
        done_units=0,
        failed_units=0,
        started_by=caller.user_id,
        created_at=datetime.now(timezone.utc),
    )
    session.add(run)
    await session.flush()
    await materialize_run(session, run)
    await session.commit()

    # Make sure the worker is up; it'll pick the run up on its next claim.
    start_worker()

    return RunCreateOut(
        run_id=run.id,
        document_count=len(doc_ids),
        unit_count=unit_count,
        status="started",
    )


@router.get("/runs", response_model=list[RunOut])
async def list_runs(
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.execute(
            select(IngestionRun)
            .order_by(IngestionRun.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return rows


@router.get("/runs/{run_id}", response_model=RunOut)
async def get_run(run_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    row = await session.get(IngestionRun, run_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    return row


@router.get("/runs/{run_id}/units", response_model=list[RunUnitOut])
async def list_run_units(
    run_id: uuid.UUID,
    status_filter: str | None = None,
    limit: int = 200,
    session: AsyncSession = Depends(get_session),
):
    stmt = (
        select(IngestionRunUnit)
        .where(IngestionRunUnit.run_id == run_id)
        .order_by(IngestionRunUnit.created_at)
        .limit(limit)
    )
    if status_filter:
        stmt = stmt.where(IngestionRunUnit.status == status_filter)
    rows = (await session.execute(stmt)).scalars().all()
    return rows


# ────────────────────── per-document syntactic sugar ──────────────────────
class ReprocessOneIn(BaseModel):
    stages: list[Stage]


@router.post(
    "/documents/{doc_id}/reprocess",
    response_model=RunCreateOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("grove.admin:all"))],
)
async def reprocess_document(
    doc_id: uuid.UUID,
    payload: ReprocessOneIn,
    session: AsyncSession = Depends(get_session),
    caller: CallerIdentity = Depends(get_caller),
):
    """Reprocess a single document — creates a 1-doc ingestion run."""
    run = IngestionRun(
        status="pending",
        stages=list(payload.stages),
        filter=RunFilter(document_ids=[doc_id]).model_dump(mode="json"),
        total_units=len(payload.stages),
        done_units=0,
        failed_units=0,
        started_by=caller.user_id,
        created_at=datetime.now(timezone.utc),
    )
    session.add(run)
    await session.flush()
    await materialize_run(session, run)
    await session.commit()
    start_worker()
    return RunCreateOut(
        run_id=run.id,
        document_count=1,
        unit_count=len(payload.stages),
        status="started",
    )
