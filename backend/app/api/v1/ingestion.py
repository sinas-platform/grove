"""Ingestion writes — called by Grove agents during the ingestion pipeline.

The post-upload function in the Sinas package POSTs to /ingest/post-upload to
register a new document; from there agents call set_document_class,
set_document_summary, set_property_value, etc.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CallerIdentity, get_caller, require_permission
from app.db import get_session
from app.models import (
    Document,
    DocumentVersion,
    Entity,
    EntityMention,
    PropertyValue,
    Relationship,
    RelationshipProposal,
)
from app.schemas.runtime import (
    PropertyValueIn,
    PropertyValueOut,
    RelationshipIn,
    RelationshipOut,
    RelationshipProposalIn,
    RelationshipProposalOut,
)

router = APIRouter(prefix="/ingest", tags=["ingestion"])


# ─────────────────────────── post-upload registration ───────────────────────────
class PostUploadIn(BaseModel):
    collection_file_id: str
    filename: str
    # content_md is optional: only text uploads carry it. Binary formats
    # (.docx, .pdf, etc.) are registered without text and rely on a later
    # extraction step to populate the version's content.
    content_md: str | None = None
    metadata: dict | None = None


class PostUploadOut(BaseModel):
    document_id: uuid.UUID
    document_version_id: uuid.UUID
    version: int


@router.post(
    "/post-upload",
    response_model=PostUploadOut,
    status_code=status.HTTP_201_CREATED,
)
async def post_upload(
    payload: PostUploadIn,
    session: AsyncSession = Depends(get_session),
    caller: CallerIdentity = Depends(get_caller),
):
    """Called by the Sinas post-upload function to register a new document.

    Idempotent on `collection_file_id`: re-uploads create new versions, not
    duplicate documents.
    """
    existing = (
        await session.execute(
            select(Document).where(Document.collection_file_id == payload.collection_file_id)
        )
    ).scalar_one_or_none()

    if existing is None:
        doc = Document(
            filename=payload.filename,
            collection_file_id=payload.collection_file_id,
            owner_id=caller.user_id,
            roles=caller.roles or [],
        )
        session.add(doc)
        await session.flush()
        version = 1
    else:
        doc = existing
        latest = (
            await session.execute(
                select(DocumentVersion)
                .where(DocumentVersion.document_id == doc.id)
                .order_by(DocumentVersion.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        version = (latest.version + 1) if latest else 1

    # Postgres TEXT can't store NUL bytes — strip them defensively even for
    # nominally-text uploads where one snuck through. Also defend against
    # the U+0000 escape that some markdown converters emit for binary residue.
    content_md = (
        payload.content_md.replace("\x00", "").replace("\\u0000", "")
        if payload.content_md
        else None
    )
    dv = DocumentVersion(document_id=doc.id, version=version, content_md=content_md)
    session.add(dv)
    await session.flush()

    doc.current_version_id = dv.id
    await session.commit()
    return PostUploadOut(document_id=doc.id, document_version_id=dv.id, version=version)


# ─────────────────────────── classifier ───────────────────────────
class SetClassIn(BaseModel):
    document_class_id: uuid.UUID
    confidence: float | None = None
    reason: str | None = None


@router.post(
    "/documents/{doc_id}/class",
    dependencies=[Depends(require_permission("grove.ingestion.write:own"))],
)
async def set_document_class(
    doc_id: uuid.UUID,
    payload: SetClassIn,
    session: AsyncSession = Depends(get_session),
):
    doc = await session.get(Document, doc_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    doc.document_class_id = payload.document_class_id
    doc.classification_confidence = payload.confidence
    await session.commit()
    return {"ok": True}


# ─────────────────────────── summarizer ───────────────────────────
class SetSummaryIn(BaseModel):
    summary: str
    toc: dict | None = None


@router.post(
    "/documents/{doc_id}/summary",
    dependencies=[Depends(require_permission("grove.ingestion.write:own"))],
)
async def set_document_summary(
    doc_id: uuid.UUID,
    payload: SetSummaryIn,
    session: AsyncSession = Depends(get_session),
):
    doc = await session.get(Document, doc_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    doc.summary = payload.summary
    doc.toc = payload.toc
    await session.commit()
    return {"ok": True}


# ─────────────────────────── properties ───────────────────────────
@router.post(
    "/property-values",
    response_model=PropertyValueOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("grove.ingestion.write:own"))],
)
async def set_property_value(
    payload: PropertyValueIn,
    session: AsyncSession = Depends(get_session),
):
    raw = payload.model_dump()
    if isinstance(raw["value"], (str, int, float, bool)) or raw["value"] is None:
        raw["value"] = {"_": raw["value"]}
    elif isinstance(raw["value"], list):
        raw["value"] = {"_": raw["value"]}
    if raw.get("source_span") is not None:
        raw["source_span"] = raw["source_span"]
    row = PropertyValue(**raw)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


# ─────────────────────────── entities ───────────────────────────
class EntityIn(BaseModel):
    entity_type_id: uuid.UUID
    canonical_form: str
    extra_metadata: dict | None = None


@router.post(
    "/entities",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("grove.ingestion.write:own"))],
)
async def propose_new_entity(
    payload: EntityIn, session: AsyncSession = Depends(get_session)
):
    row = Entity(
        entity_type_id=payload.entity_type_id,
        canonical_form=payload.canonical_form,
        extra_metadata=payload.extra_metadata,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return {"id": row.id, "canonical_form": row.canonical_form}


class EntityMentionInWithBody(BaseModel):
    document_id: uuid.UUID
    document_version_id: uuid.UUID | None = None
    entity_id: uuid.UUID
    span: dict
    confidence: float | None = None


@router.post(
    "/entity-mentions",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("grove.ingestion.write:own"))],
)
async def record_entity_mention(
    payload: EntityMentionInWithBody, session: AsyncSession = Depends(get_session)
):
    row = EntityMention(**payload.model_dump())
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return {"id": row.id}


# ─────────────────────────── relationships ───────────────────────────
@router.post(
    "/relationships",
    response_model=RelationshipOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("grove.ingestion.write:own"))],
)
async def record_relationship(
    payload: RelationshipIn, session: AsyncSession = Depends(get_session)
):
    data = payload.model_dump()
    span = data.pop("evidence_span", None)
    row = Relationship(
        **data, evidence_span=span, last_verified_at=datetime.now(timezone.utc)
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


@router.post(
    "/relationship-proposals",
    response_model=RelationshipProposalOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("grove.ingestion.write:own"))],
)
async def propose_relationship(
    payload: RelationshipProposalIn, session: AsyncSession = Depends(get_session)
):
    row = RelationshipProposal(**payload.model_dump(), status="pending")
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row
