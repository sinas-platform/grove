"""Shared introspect implementation.

The same distributions math is invoked from two places:
  - `POST /retrieval/introspect` — stateless, caller passes a GroveFilter
  - `POST /retrieval/results/{id}/introspect` — uses Result.filter
Both call `introspect_with_filter` so the math has one home.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CallerIdentity
from app.models import Document, DocumentClassProperty, PropertyValue
from app.schemas.runtime import (
    GroveFilter,
    IntrospectFieldDistribution,
    IntrospectOut,
)
from app.services.visibility import visible_clause


def _unwrap_property_value(v: Any) -> Any:
    if isinstance(v, dict) and set(v.keys()) == {"_"}:
        return v["_"]
    return v


def apply_grove_filter(stmt, f: GroveFilter):
    """Apply the subset of GroveFilter clauses we currently support in queries.

    field_filters / regex_filters / dossier scoping are intentionally NOT
    applied here (see ARCHITECTURE.md §13). They live on the filter row for
    agent inspection and downstream consumers, but the count subquery only
    narrows by class, explicit excludes, FTS, and staged state.
    """
    stmt = stmt.where(Document.staged.is_(False))
    if f.document_class_id is not None:
        stmt = stmt.where(Document.document_class_id == f.document_class_id)
    if f.explicit_excludes:
        stmt = stmt.where(~Document.id.in_(f.explicit_excludes))
    if f.text_search:
        from sqlalchemy import text as sql_text

        stmt = stmt.where(
            Document.id.in_(
                select(sql_text("document_version.document_id"))
                .select_from(sql_text("document_version"))
                .where(sql_text("content_tsvector @@ plainto_tsquery(:q)"))
                .params(q=f.text_search)
            )
        )
    return stmt


async def count_candidates(
    session: AsyncSession, caller: CallerIdentity, f: GroveFilter
) -> int:
    read_all = await caller.has_permission("grove.documents.read:all")
    base = select(Document.id).where(visible_clause(Document, caller, read_all=read_all))
    base = apply_grove_filter(base, f)
    total = (
        await session.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()
    return int(total)


async def introspect_with_filter(
    session: AsyncSession,
    caller: CallerIdentity,
    f: GroveFilter,
    fields: list[str] | None,
    top_k: int,
) -> IntrospectOut:
    read_all = await caller.has_permission("grove.documents.read:all")
    base = select(Document.id).where(visible_clause(Document, caller, read_all=read_all))
    base = apply_grove_filter(base, f)
    total = (
        await session.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()

    distributions: list[IntrospectFieldDistribution] = []
    if f.document_class_id is not None:
        properties = (
            await session.execute(
                select(DocumentClassProperty).where(
                    DocumentClassProperty.document_class_id == f.document_class_id
                )
            )
        ).scalars().all()
        for prop in properties:
            if fields and prop.name not in fields:
                continue
            counts = (
                await session.execute(
                    select(PropertyValue.value, func.count().label("n"))
                    .where(PropertyValue.property_id == prop.id)
                    .where(PropertyValue.document_id.in_(base))
                    .group_by(PropertyValue.value)
                    .order_by(func.count().desc())
                    .limit(top_k)
                )
            ).all()
            distributions.append(
                IntrospectFieldDistribution(
                    field=prop.name,
                    values=[
                        {"value": _unwrap_property_value(c[0]), "count": int(c[1])}
                        for c in counts
                    ],
                    total_documents=total,
                )
            )
    return IntrospectOut(candidate_count=total, distributions=distributions)
