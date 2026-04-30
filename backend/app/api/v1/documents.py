"""Document and document-version reads with visibility filtering."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CallerIdentity, get_caller, require_permission
from app.db import get_session
from app.models import (
    Document,
    DocumentVersion,
    Dossier,
    DossierDocument,
    Entity,
    EntityMention,
    EntityType,
    PropertyValue,
)
from app.schemas.runtime import (
    DocumentOut,
    DocumentPatch,
    DocumentVersionOut,
    EntityMentionWithEntityOut,
    PropertyValueOut,
)
from app.services.visibility import visible_clause

router = APIRouter(prefix="/documents", tags=["documents"])


async def _document_visibility(model, caller: CallerIdentity):
    read_all = await caller.has_permission("grove.documents.read:all")
    base = visible_clause(model, caller, read_all=read_all)
    if read_all or caller.is_admin:
        return base
    # Documents reachable through a visible dossier (cascading access)
    from sqlalchemy import or_

    dossier_subq = (
        select(DossierDocument.document_id)
        .join(Dossier, Dossier.id == DossierDocument.dossier_id)
        .where(visible_clause(Dossier, caller, read_all=False))
    )
    return or_(base, model.id.in_(dossier_subq))


@router.get("", response_model=list[DocumentOut])
async def list_documents(
    document_class_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
    caller: CallerIdentity = Depends(get_caller),
):
    stmt = select(Document).where(await _document_visibility(Document, caller))
    if document_class_id is not None:
        stmt = stmt.where(Document.document_class_id == document_class_id)
    stmt = stmt.order_by(desc(Document.created_at)).limit(limit).offset(offset)
    return (await session.execute(stmt)).scalars().all()


@router.get("/{doc_id}", response_model=DocumentOut)
async def get_document(
    doc_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    caller: CallerIdentity = Depends(get_caller),
):
    stmt = (
        select(Document)
        .where(Document.id == doc_id)
        .where(await _document_visibility(Document, caller))
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    return row


@router.patch(
    "/{doc_id}",
    response_model=DocumentOut,
    dependencies=[Depends(require_permission("grove.documents.write:own"))],
)
async def update_document(
    doc_id: uuid.UUID,
    payload: DocumentPatch,
    session: AsyncSession = Depends(get_session),
    caller: CallerIdentity = Depends(get_caller),
):
    row = await session.get(Document, doc_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    write_all = await caller.has_permission("grove.documents.write:all")
    if not (write_all or caller.is_admin or row.owner_id == caller.user_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not your document")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    await session.commit()
    await session.refresh(row)
    return row


@router.get("/{doc_id}/versions", response_model=list[DocumentVersionOut])
async def list_document_versions(
    doc_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    caller: CallerIdentity = Depends(get_caller),
):
    # Visibility check on the document itself
    parent = (
        await session.execute(
            select(Document)
            .where(Document.id == doc_id)
            .where(await _document_visibility(Document, caller))
        )
    ).scalar_one_or_none()
    if parent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    rows = (
        await session.execute(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == doc_id)
            .order_by(DocumentVersion.version)
        )
    ).scalars().all()
    return rows


@router.get("/{doc_id}/versions/{version}/content")
async def read_document_content(
    doc_id: uuid.UUID,
    version: int,
    line_from: int | None = Query(default=None, ge=1),
    line_to: int | None = Query(default=None, ge=1),
    session: AsyncSession = Depends(get_session),
    caller: CallerIdentity = Depends(get_caller),
):
    parent = (
        await session.execute(
            select(Document)
            .where(Document.id == doc_id)
            .where(await _document_visibility(Document, caller))
        )
    ).scalar_one_or_none()
    if parent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    dv = (
        await session.execute(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == doc_id)
            .where(DocumentVersion.version == version)
        )
    ).scalar_one_or_none()
    if dv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "version not found")
    # The version row exists; content may still be empty if extraction
    # hasn't run / failed. Tell the caller explicitly instead of 404'ing
    # so an agent can route around the missing text.
    if dv.content_md is None:
        return {
            "document_id": doc_id,
            "version": version,
            "line_from": line_from,
            "line_to": line_to,
            "content": "",
            "extracted": False,
            "note": "version exists but no extracted text — post-upload extraction may have failed or hasn't run yet",
        }
    content = dv.content_md
    if line_from or line_to:
        lines = content.splitlines()
        start = (line_from - 1) if line_from else 0
        end = line_to if line_to else len(lines)
        content = "\n".join(lines[start:end])
    return {
        "document_id": doc_id,
        "version": version,
        "line_from": line_from,
        "line_to": line_to,
        "content": content,
        "extracted": True,
    }


@router.get("/{doc_id}/property-values", response_model=list[PropertyValueOut])
async def list_property_values(
    doc_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    caller: CallerIdentity = Depends(get_caller),
):
    parent = (
        await session.execute(
            select(Document)
            .where(Document.id == doc_id)
            .where(await _document_visibility(Document, caller))
        )
    ).scalar_one_or_none()
    if parent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    rows = (
        await session.execute(
            select(PropertyValue).where(PropertyValue.document_id == doc_id)
        )
    ).scalars().all()
    return rows


@router.get(
    "/{doc_id}/entity-mentions", response_model=list[EntityMentionWithEntityOut]
)
async def list_entity_mentions(
    doc_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    caller: CallerIdentity = Depends(get_caller),
):
    parent = (
        await session.execute(
            select(Document)
            .where(Document.id == doc_id)
            .where(await _document_visibility(Document, caller))
        )
    ).scalar_one_or_none()
    if parent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    rows = (
        await session.execute(
            select(EntityMention, Entity, EntityType)
            .join(Entity, Entity.id == EntityMention.entity_id)
            .join(EntityType, EntityType.id == Entity.entity_type_id)
            .where(EntityMention.document_id == doc_id)
            .order_by(EntityMention.created_at)
        )
    ).all()
    return [
        EntityMentionWithEntityOut(
            id=mention.id,
            created_at=mention.created_at,
            updated_at=mention.updated_at,
            document_id=mention.document_id,
            document_version_id=mention.document_version_id,
            entity_id=mention.entity_id,
            span=mention.span,
            confidence=mention.confidence,
            entity_canonical_form=entity.canonical_form,
            entity_type_id=entity.entity_type_id,
            entity_type_name=etype.name,
        )
        for mention, entity, etype in rows
    ]
