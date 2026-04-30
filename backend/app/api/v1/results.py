"""Result reads — for the admin UI and downstream synthesis."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CallerIdentity, get_caller
from app.db import get_session
from app.models import Result, ResultDocument, ResultTrace
from app.schemas.common import TraceOut
from app.schemas.runtime import ResultDocumentOut, ResultOut
from app.services.visibility import visible_clause

router = APIRouter(prefix="/results", tags=["results"])


@router.get("", response_model=list[ResultOut])
async def list_results(
    session: AsyncSession = Depends(get_session),
    caller: CallerIdentity = Depends(get_caller),
    limit: int = 50,
    offset: int = 0,
):
    read_all = await caller.has_permission("grove.results.read:all")
    stmt = (
        select(Result)
        .where(visible_clause(Result, caller, read_all=read_all))
        .order_by(Result.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return (await session.execute(stmt)).scalars().all()


@router.get("/{result_id}", response_model=ResultOut)
async def get_result(
    result_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    caller: CallerIdentity = Depends(get_caller),
):
    read_all = await caller.has_permission("grove.results.read:all")
    stmt = (
        select(Result)
        .where(Result.id == result_id)
        .where(visible_clause(Result, caller, read_all=read_all))
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "result not found")
    return row


@router.get("/{result_id}/documents", response_model=list[ResultDocumentOut])
async def get_result_documents(
    result_id: uuid.UUID, session: AsyncSession = Depends(get_session)
):
    rows = (
        await session.execute(
            select(ResultDocument)
            .where(ResultDocument.result_id == result_id)
            .order_by(ResultDocument.rank.nulls_last())
        )
    ).scalars().all()
    return rows


@router.get("/{result_id}/trace", response_model=list[TraceOut])
async def get_trace(result_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    rows = (
        await session.execute(
            select(ResultTrace)
            .where(ResultTrace.result_id == result_id)
            .order_by(ResultTrace.sequence)
        )
    ).scalars().all()
    return rows
