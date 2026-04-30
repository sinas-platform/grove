"""Playbooks — proxy to Sinas Skills (management API), with scope mapping in Grove.

Skills live in Sinas (canonical storage). Grove tracks which document/dossier
classes a skill applies to via `playbook_scope`. Two namespaces are supported:
  - grove_retrieval_playbooks
  - grove_synthesis_playbooks
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CallerIdentity, get_caller, require_permission
from app.db import get_session
from app.models import PlaybookScope
from app.schemas.config import PlaybookScopeIn, PlaybookScopeOut
from app.services.sinas import Management, get_management

router = APIRouter(prefix="/playbooks", tags=["playbooks"])

PlaybookKind = Literal["retrieval", "synthesis"]
NAMESPACE_FOR = {
    "retrieval": "grove_retrieval_playbooks",
    "synthesis": "grove_synthesis_playbooks",
}


def _require_sinas_token(caller: CallerIdentity) -> str:
    if caller.sinas_token is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "playbook management requires a Sinas token",
        )
    return caller.sinas_token


class PlaybookIn(BaseModel):
    name: str
    description: str
    content: str


class PlaybookOut(BaseModel):
    namespace: str
    name: str
    description: str | None = None
    content: str | None = None


@router.get("/{kind}", response_model=list[PlaybookOut])
async def list_playbooks(
    kind: PlaybookKind,
    caller: CallerIdentity = Depends(get_caller),
    mgmt: Management = Depends(get_management),
):
    token = _require_sinas_token(caller)
    skills = await mgmt.list_skills(token, NAMESPACE_FOR[kind])
    return [
        PlaybookOut(
            namespace=s.get("namespace", NAMESPACE_FOR[kind]),
            name=s.get("name", ""),
            description=s.get("description"),
            content=s.get("content"),
        )
        for s in skills
    ]


@router.get("/{kind}/{name}", response_model=PlaybookOut)
async def get_playbook(
    kind: PlaybookKind,
    name: str,
    caller: CallerIdentity = Depends(get_caller),
    mgmt: Management = Depends(get_management),
):
    token = _require_sinas_token(caller)
    skill = await mgmt.get_skill(token, NAMESPACE_FOR[kind], name)
    if skill is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "playbook not found")
    return PlaybookOut(
        namespace=NAMESPACE_FOR[kind],
        name=name,
        description=skill.get("description"),
        content=skill.get("content"),
    )


@router.put(
    "/{kind}/{name}",
    response_model=PlaybookOut,
    dependencies=[Depends(require_permission("grove.admin:all"))],
)
async def upsert_playbook(
    kind: PlaybookKind,
    name: str,
    payload: PlaybookIn,
    caller: CallerIdentity = Depends(get_caller),
    mgmt: Management = Depends(get_management),
):
    if payload.name != name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "path name must match payload.name")
    token = _require_sinas_token(caller)
    skill = await mgmt.upsert_skill(
        token, NAMESPACE_FOR[kind], name, payload.description, payload.content
    )
    return PlaybookOut(
        namespace=NAMESPACE_FOR[kind],
        name=name,
        description=skill.get("description"),
        content=skill.get("content"),
    )


@router.delete(
    "/{kind}/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("grove.admin:all"))],
)
async def delete_playbook(
    kind: PlaybookKind,
    name: str,
    caller: CallerIdentity = Depends(get_caller),
    mgmt: Management = Depends(get_management),
    session: AsyncSession = Depends(get_session),
):
    token = _require_sinas_token(caller)
    await mgmt.delete_skill(token, NAMESPACE_FOR[kind], name)
    rows = (
        await session.execute(
            select(PlaybookScope)
            .where(PlaybookScope.skill_namespace == NAMESPACE_FOR[kind])
            .where(PlaybookScope.skill_name == name)
        )
    ).scalars().all()
    for r in rows:
        await session.delete(r)
    await session.commit()


# ─────────────────────────── scope ───────────────────────────
@router.get("/{kind}/{name}/scope", response_model=list[PlaybookScopeOut])
async def get_playbook_scope(
    kind: PlaybookKind, name: str, session: AsyncSession = Depends(get_session)
):
    rows = (
        await session.execute(
            select(PlaybookScope)
            .where(PlaybookScope.skill_namespace == NAMESPACE_FOR[kind])
            .where(PlaybookScope.skill_name == name)
        )
    ).scalars().all()
    return rows


@router.post(
    "/{kind}/{name}/scope",
    response_model=PlaybookScopeOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("grove.admin:all"))],
)
async def add_playbook_scope(
    kind: PlaybookKind,
    name: str,
    payload: PlaybookScopeIn,
    session: AsyncSession = Depends(get_session),
):
    row = PlaybookScope(
        skill_namespace=NAMESPACE_FOR[kind],
        skill_name=name,
        document_class_id=payload.document_class_id,
        dossier_class_id=payload.dossier_class_id,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


@router.delete(
    "/scope/{scope_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("grove.admin:all"))],
)
async def delete_playbook_scope(
    scope_id: uuid.UUID, session: AsyncSession = Depends(get_session)
):
    row = await session.get(PlaybookScope, scope_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "scope row not found")
    await session.delete(row)
    await session.commit()
