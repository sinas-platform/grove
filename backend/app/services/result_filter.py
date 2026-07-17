"""Result-filter mutation service.

All filter mutations route through `_mutate_filter`, which:
  1. Loads the Result and verifies it's a draft (published results are frozen).
  2. Checks the caller-supplied `filter_version` against the row (409 on
     mismatch — optimistic concurrency).
  3. Applies the caller-supplied transform to a `GroveFilter`.
  4. Computes before/after candidate counts (visibility-scoped).
  5. Writes a `ResultTrace` row attributed to `"grove"` with mechanical
     outcome (filter before/after, candidate count delta).
  6. Persists the new filter + bumped version.
  7. Returns `FilterMutationOut`.

Per-agent reasoning entries are still written via the existing
`append_trace` op — those are agent-discipline. Mutation auto-traces are
server-enforced fact.

See ADR `docs/adrs/2026-05-14-stateful-filter-on-result.md`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CallerIdentity
from app.models import (
    Result,
    ResultDocument,
    ResultTrace,
)
from app.schemas.result_filter import FilterMutationOut
from app.schemas.runtime import (
    EntityFilter,
    FieldFilter,
    GroveFilter,
    IntrospectOut,
    RegexFilter,
)
from app.services.introspect import count_candidates, introspect_with_filter
from app.services.visibility import visible_clause


# Author attribution for server-written trace rows. Agents writing narrative
# entries via append_trace use their own namespace/name; "grove" here is the
# bright-line marker for "the server did this in response to a mutation op."
AUTO_TRACE_AGENT = "grove"


# ───────────────────────────── core helpers ─────────────────────────────


async def load_visible_result(
    session: AsyncSession,
    caller: CallerIdentity,
    result_id: uuid.UUID,
    *,
    for_write: bool,
) -> Result:
    """Load a result the caller may see (or, `for_write`, mutate): owner,
    shared role, or the corresponding `:all` permission. 404 on rows outside
    that scope so invisible ids don't leak existence. The `write:own` /
    `read:own` permission gate stays at the route layer; this enforces the
    "own" part the gate alone cannot."""
    lift = await caller.has_permission(
        "grove.results.write:all" if for_write else "grove.results.read:all"
    )
    row = (
        await session.execute(
            select(Result)
            .where(Result.id == result_id)
            .where(visible_clause(Result, caller, read_all=lift))
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "result not found")
    return row


async def _load_draft_result(
    session: AsyncSession, caller: CallerIdentity, result_id: uuid.UUID
) -> Result:
    row = await load_visible_result(session, caller, result_id, for_write=True)
    if row.status == "published":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "result is published; filter is frozen",
        )
    return row


# Candidate counting + introspect distributions live in services/introspect.py
# so the math has one home. We just import and call.


async def _next_trace_sequence(session: AsyncSession, result_id: uuid.UUID) -> int:
    cur_max = (
        await session.execute(
            select(func.coalesce(func.max(ResultTrace.sequence), 0)).where(
                ResultTrace.result_id == result_id
            )
        )
    ).scalar_one()
    return int(cur_max) + 1


async def _mutate_filter(
    session: AsyncSession,
    caller: CallerIdentity,
    result_id: uuid.UUID,
    expected_version: int,
    action: str,
    parameters: dict[str, Any],
    transform: Callable[[GroveFilter], GroveFilter],
) -> FilterMutationOut:
    result = await _load_draft_result(session, caller, result_id)
    if result.filter_version != expected_version:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"filter_version mismatch: server has {result.filter_version}, "
            f"caller sent {expected_version}",
        )

    before_filter = GroveFilter(**(result.filter or {}))
    before_count = await count_candidates(session, caller, before_filter)

    after_filter = transform(before_filter)
    after_count = await count_candidates(session, caller, after_filter)

    result.filter = after_filter.model_dump(mode="json")
    result.filter_version += 1

    next_seq = await _next_trace_sequence(session, result_id)
    session.add(
        ResultTrace(
            result_id=result_id,
            sequence=next_seq,
            agent=AUTO_TRACE_AGENT,
            action=action,
            parameters=parameters,
            outcome={
                "filter_before": before_filter.model_dump(mode="json"),
                "filter_after": after_filter.model_dump(mode="json"),
                "candidate_count_before": before_count,
                "candidate_count_after": after_count,
            },
            occurred_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()
    await session.refresh(result)

    return FilterMutationOut(
        filter_version=result.filter_version,
        filter=after_filter,
        candidate_count=after_count,
        trace_sequence=next_seq,
    )


# Convenience: small validators used by multiple ops.
_LIST_OPS = {"in", "neq"}


def _validate_field_filter_op_values(
    op: str, values: list[Any] | None, value: Any
) -> None:
    if op in _LIST_OPS:
        if values is None and value is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"field filter op='{op}' requires `values` (or a `value` "
                "to be implicitly wrapped)",
            )
    else:
        if value is None and not values:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"field filter op='{op}' requires `value` (single scalar)",
            )


# ───────────────────────────── scope ops ─────────────────────────────


async def set_document_class_filter(
    session, caller, result_id, expected_version, document_class_id
) -> FilterMutationOut:
    def t(f: GroveFilter) -> GroveFilter:
        return f.model_copy(update={"document_class_id": document_class_id})

    return await _mutate_filter(
        session, caller, result_id, expected_version,
        action="set_document_class_filter",
        parameters={"document_class_id": str(document_class_id)},
        transform=t,
    )


async def clear_document_class_filter(
    session, caller, result_id, expected_version
) -> FilterMutationOut:
    def t(f: GroveFilter) -> GroveFilter:
        return f.model_copy(update={"document_class_id": None})

    return await _mutate_filter(
        session, caller, result_id, expected_version,
        action="clear_document_class_filter",
        parameters={},
        transform=t,
    )


async def set_dossier_filter(
    session, caller, result_id, expected_version, dossier_id
) -> FilterMutationOut:
    def t(f: GroveFilter) -> GroveFilter:
        return f.model_copy(update={"dossier_id": dossier_id})

    return await _mutate_filter(
        session, caller, result_id, expected_version,
        action="set_dossier_filter",
        parameters={"dossier_id": str(dossier_id)},
        transform=t,
    )


async def set_dossier_class_filter(
    session, caller, result_id, expected_version, dossier_class_id
) -> FilterMutationOut:
    def t(f: GroveFilter) -> GroveFilter:
        return f.model_copy(update={"dossier_class_id": dossier_class_id})

    return await _mutate_filter(
        session, caller, result_id, expected_version,
        action="set_dossier_class_filter",
        parameters={"dossier_class_id": str(dossier_class_id)},
        transform=t,
    )


async def clear_dossier_filters(
    session, caller, result_id, expected_version
) -> FilterMutationOut:
    def t(f: GroveFilter) -> GroveFilter:
        return f.model_copy(update={"dossier_id": None, "dossier_class_id": None})

    return await _mutate_filter(
        session, caller, result_id, expected_version,
        action="clear_dossier_filters",
        parameters={},
        transform=t,
    )


# ───────────────────────────── field-filter ops ─────────────────────────────


def _without_field(filters: list[FieldFilter], field: str) -> list[FieldFilter]:
    return [ff for ff in filters if ff.field != field]


def _find_field(filters: list[FieldFilter], field: str) -> FieldFilter | None:
    return next((ff for ff in filters if ff.field == field), None)


async def set_field_filter(
    session, caller, result_id, expected_version,
    field: str, op: str, values: list[Any] | None, value: Any, join: str,
) -> FilterMutationOut:
    _validate_field_filter_op_values(op, values, value)

    def t(f: GroveFilter) -> GroveFilter:
        new_ff = FieldFilter(field=field, op=op, values=values, value=value, join=join)
        updated = _without_field(f.field_filters, field) + [new_ff]
        return f.model_copy(update={"field_filters": updated})

    return await _mutate_filter(
        session, caller, result_id, expected_version,
        action="set_field_filter",
        parameters={"field": field, "op": op, "values": values, "value": value, "join": join},
        transform=t,
    )


async def extend_field_filter_values(
    session, caller, result_id, expected_version, field: str, values: list[Any]
) -> FilterMutationOut:
    def t(f: GroveFilter) -> GroveFilter:
        existing = _find_field(f.field_filters, field)
        if existing is None:
            # auto-create with op="in"
            new_ff = FieldFilter(field=field, op="in", values=list(values), join="and")
        elif existing.op not in _LIST_OPS:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"cannot extend values on field='{field}' with op='{existing.op}' "
                "(only 'in'/'neq' have multi-value semantics)",
            )
        else:
            merged = list(existing.values or []) + [
                v for v in values if v not in (existing.values or [])
            ]
            new_ff = existing.model_copy(update={"values": merged})
        updated = _without_field(f.field_filters, field) + [new_ff]
        return f.model_copy(update={"field_filters": updated})

    return await _mutate_filter(
        session, caller, result_id, expected_version,
        action="extend_field_filter_values",
        parameters={"field": field, "values": values},
        transform=t,
    )


async def shrink_field_filter_values(
    session, caller, result_id, expected_version, field: str, values: list[Any]
) -> FilterMutationOut:
    def t(f: GroveFilter) -> GroveFilter:
        existing = _find_field(f.field_filters, field)
        if existing is None:
            # No-op: shrinking a non-existent filter is harmless.
            return f
        if existing.op not in _LIST_OPS:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"cannot shrink values on field='{field}' with op='{existing.op}'",
            )
        remaining = [v for v in (existing.values or []) if v not in values]
        updated = _without_field(f.field_filters, field)
        if remaining:
            updated.append(existing.model_copy(update={"values": remaining}))
        return f.model_copy(update={"field_filters": updated})

    return await _mutate_filter(
        session, caller, result_id, expected_version,
        action="shrink_field_filter_values",
        parameters={"field": field, "values": values},
        transform=t,
    )


async def remove_field_filter(
    session, caller, result_id, expected_version, field: str
) -> FilterMutationOut:
    def t(f: GroveFilter) -> GroveFilter:
        return f.model_copy(update={"field_filters": _without_field(f.field_filters, field)})

    return await _mutate_filter(
        session, caller, result_id, expected_version,
        action="remove_field_filter",
        parameters={"field": field},
        transform=t,
    )


# ───────────────────────────── entity-filter ops ─────────────────────────────


def _without_entity_slot(
    filters: list[EntityFilter], entity_type_id: uuid.UUID | None
) -> list[EntityFilter]:
    return [ef for ef in filters if ef.entity_type_id != entity_type_id]


def _find_entity_slot(
    filters: list[EntityFilter], entity_type_id: uuid.UUID | None
) -> EntityFilter | None:
    return next((ef for ef in filters if ef.entity_type_id == entity_type_id), None)


async def set_entity_filter(
    session, caller, result_id, expected_version,
    entity_type_id: uuid.UUID | None, entity_ids: list[uuid.UUID],
) -> FilterMutationOut:
    def t(f: GroveFilter) -> GroveFilter:
        new_ef = EntityFilter(entity_type_id=entity_type_id, entity_ids=entity_ids)
        updated = _without_entity_slot(f.entity_filters, entity_type_id) + [new_ef]
        return f.model_copy(update={"entity_filters": updated})

    return await _mutate_filter(
        session, caller, result_id, expected_version,
        action="set_entity_filter",
        parameters={
            "entity_type_id": str(entity_type_id) if entity_type_id else None,
            "entity_ids": [str(x) for x in entity_ids],
        },
        transform=t,
    )


async def extend_entity_filter(
    session, caller, result_id, expected_version,
    entity_type_id: uuid.UUID | None, entity_ids: list[uuid.UUID],
) -> FilterMutationOut:
    def t(f: GroveFilter) -> GroveFilter:
        existing = _find_entity_slot(f.entity_filters, entity_type_id)
        if existing is None:
            new_ef = EntityFilter(entity_type_id=entity_type_id, entity_ids=list(entity_ids))
        else:
            merged = list(existing.entity_ids) + [
                e for e in entity_ids if e not in existing.entity_ids
            ]
            new_ef = existing.model_copy(update={"entity_ids": merged})
        updated = _without_entity_slot(f.entity_filters, entity_type_id) + [new_ef]
        return f.model_copy(update={"entity_filters": updated})

    return await _mutate_filter(
        session, caller, result_id, expected_version,
        action="extend_entity_filter",
        parameters={
            "entity_type_id": str(entity_type_id) if entity_type_id else None,
            "entity_ids": [str(x) for x in entity_ids],
        },
        transform=t,
    )


async def shrink_entity_filter(
    session, caller, result_id, expected_version,
    entity_type_id: uuid.UUID | None, entity_ids: list[uuid.UUID],
) -> FilterMutationOut:
    def t(f: GroveFilter) -> GroveFilter:
        existing = _find_entity_slot(f.entity_filters, entity_type_id)
        if existing is None:
            return f
        remaining = [e for e in existing.entity_ids if e not in entity_ids]
        updated = _without_entity_slot(f.entity_filters, entity_type_id)
        if remaining:
            updated.append(existing.model_copy(update={"entity_ids": remaining}))
        return f.model_copy(update={"entity_filters": updated})

    return await _mutate_filter(
        session, caller, result_id, expected_version,
        action="shrink_entity_filter",
        parameters={
            "entity_type_id": str(entity_type_id) if entity_type_id else None,
            "entity_ids": [str(x) for x in entity_ids],
        },
        transform=t,
    )


async def remove_entity_filter(
    session, caller, result_id, expected_version,
    entity_type_id: uuid.UUID | None,
) -> FilterMutationOut:
    def t(f: GroveFilter) -> GroveFilter:
        return f.model_copy(
            update={"entity_filters": _without_entity_slot(f.entity_filters, entity_type_id)}
        )

    return await _mutate_filter(
        session, caller, result_id, expected_version,
        action="remove_entity_filter",
        parameters={"entity_type_id": str(entity_type_id) if entity_type_id else None},
        transform=t,
    )


# ───────────────────────────── regex-filter ops ─────────────────────────────


def _without_regex_field(filters: list[RegexFilter], field: str) -> list[RegexFilter]:
    return [rf for rf in filters if rf.field != field]


async def set_regex_filter(
    session, caller, result_id, expected_version, field: str, pattern: str
) -> FilterMutationOut:
    def t(f: GroveFilter) -> GroveFilter:
        updated = _without_regex_field(f.regex_filters, field) + [
            RegexFilter(field=field, pattern=pattern)
        ]
        return f.model_copy(update={"regex_filters": updated})

    return await _mutate_filter(
        session, caller, result_id, expected_version,
        action="set_regex_filter",
        parameters={"field": field, "pattern": pattern},
        transform=t,
    )


async def remove_regex_filter(
    session, caller, result_id, expected_version, field: str
) -> FilterMutationOut:
    def t(f: GroveFilter) -> GroveFilter:
        return f.model_copy(
            update={"regex_filters": _without_regex_field(f.regex_filters, field)}
        )

    return await _mutate_filter(
        session, caller, result_id, expected_version,
        action="remove_regex_filter",
        parameters={"field": field},
        transform=t,
    )


# ───────────────────────────── text-search ops ─────────────────────────────


async def set_text_search(
    session, caller, result_id, expected_version, query: str
) -> FilterMutationOut:
    def t(f: GroveFilter) -> GroveFilter:
        return f.model_copy(update={"text_search": query})

    return await _mutate_filter(
        session, caller, result_id, expected_version,
        action="set_text_search",
        parameters={"query": query},
        transform=t,
    )


async def clear_text_search(
    session, caller, result_id, expected_version
) -> FilterMutationOut:
    def t(f: GroveFilter) -> GroveFilter:
        return f.model_copy(update={"text_search": None})

    return await _mutate_filter(
        session, caller, result_id, expected_version,
        action="clear_text_search",
        parameters={},
        transform=t,
    )


# ───────────────────────────── explicit includes/excludes ops ─────────────────────────────


def _dedup_extend(existing: list[uuid.UUID], add: list[uuid.UUID]) -> list[uuid.UUID]:
    seen = set(existing)
    out = list(existing)
    for v in add:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


async def add_explicit_includes(
    session, caller, result_id, expected_version, document_ids: list[uuid.UUID]
) -> FilterMutationOut:
    def t(f: GroveFilter) -> GroveFilter:
        merged = _dedup_extend(list(f.explicit_includes), list(document_ids))
        return f.model_copy(update={"explicit_includes": merged})

    return await _mutate_filter(
        session, caller, result_id, expected_version,
        action="add_explicit_includes",
        parameters={"document_ids": [str(d) for d in document_ids]},
        transform=t,
    )


async def remove_explicit_includes(
    session, caller, result_id, expected_version, document_ids: list[uuid.UUID]
) -> FilterMutationOut:
    def t(f: GroveFilter) -> GroveFilter:
        remaining = [d for d in f.explicit_includes if d not in document_ids]
        return f.model_copy(update={"explicit_includes": remaining})

    return await _mutate_filter(
        session, caller, result_id, expected_version,
        action="remove_explicit_includes",
        parameters={"document_ids": [str(d) for d in document_ids]},
        transform=t,
    )


async def clear_explicit_includes(
    session, caller, result_id, expected_version
) -> FilterMutationOut:
    def t(f: GroveFilter) -> GroveFilter:
        return f.model_copy(update={"explicit_includes": []})

    return await _mutate_filter(
        session, caller, result_id, expected_version,
        action="clear_explicit_includes",
        parameters={},
        transform=t,
    )


async def add_explicit_excludes(
    session, caller, result_id, expected_version, document_ids: list[uuid.UUID]
) -> FilterMutationOut:
    def t(f: GroveFilter) -> GroveFilter:
        merged = _dedup_extend(list(f.explicit_excludes), list(document_ids))
        return f.model_copy(update={"explicit_excludes": merged})

    return await _mutate_filter(
        session, caller, result_id, expected_version,
        action="add_explicit_excludes",
        parameters={"document_ids": [str(d) for d in document_ids]},
        transform=t,
    )


async def remove_explicit_excludes(
    session, caller, result_id, expected_version, document_ids: list[uuid.UUID]
) -> FilterMutationOut:
    def t(f: GroveFilter) -> GroveFilter:
        remaining = [d for d in f.explicit_excludes if d not in document_ids]
        return f.model_copy(update={"explicit_excludes": remaining})

    return await _mutate_filter(
        session, caller, result_id, expected_version,
        action="remove_explicit_excludes",
        parameters={"document_ids": [str(d) for d in document_ids]},
        transform=t,
    )


async def clear_explicit_excludes(
    session, caller, result_id, expected_version
) -> FilterMutationOut:
    def t(f: GroveFilter) -> GroveFilter:
        return f.model_copy(update={"explicit_excludes": []})

    return await _mutate_filter(
        session, caller, result_id, expected_version,
        action="clear_explicit_excludes",
        parameters={},
        transform=t,
    )


# ───────────────────────────── whole-filter ops ─────────────────────────────


async def clear_filter(
    session, caller, result_id, expected_version
) -> FilterMutationOut:
    def t(_: GroveFilter) -> GroveFilter:
        return GroveFilter()

    return await _mutate_filter(
        session, caller, result_id, expected_version,
        action="clear_filter",
        parameters={},
        transform=t,
    )


async def replace_filter(
    session, caller, result_id, expected_version, new_filter: GroveFilter
) -> FilterMutationOut:
    def t(_: GroveFilter) -> GroveFilter:
        return new_filter

    return await _mutate_filter(
        session, caller, result_id, expected_version,
        action="replace_filter",
        parameters={"filter": new_filter.model_dump(mode="json")},
        transform=t,
    )


# ───────────────────────────── draft-result document ops ─────────────────────────────


async def merge_results(
    session: AsyncSession,
    caller: CallerIdentity,
    parent_id: uuid.UUID,
    child_result_ids: list[uuid.UUID],
) -> dict:
    """Merge child results into a parent, deterministically.

    Copies the union of the children's documents onto the parent (dedup by
    document_id, first-seen child wins), preserves per-document provenance
    (each copied row's reason records the source child), sets
    `parent_result_id` on every child, and writes a server-enforced trace on
    the parent recording exactly what was merged. Exists so an orchestrating
    agent never shuttles document-id lists through its own context — an LLM
    copy can silently drop rows; this cannot.
    """
    parent = await load_visible_result(session, caller, parent_id, for_write=True)

    existing: set[uuid.UUID] = set(
        (
            await session.execute(
                select(ResultDocument.document_id).where(
                    ResultDocument.result_id == parent_id
                )
            )
        ).scalars()
    )

    per_child: dict[str, dict[str, int]] = {}
    for child_id in child_result_ids:
        if child_id == parent_id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "a result cannot be merged into itself"
            )
        child = await load_visible_result(session, caller, child_id, for_write=False)
        rows = (
            await session.execute(
                select(ResultDocument)
                .where(ResultDocument.result_id == child_id)
                .order_by(ResultDocument.rank.nulls_last())
            )
        ).scalars().all()
        added = 0
        for rd in rows:
            if rd.document_id in existing:
                continue
            existing.add(rd.document_id)
            session.add(
                ResultDocument(
                    result_id=parent_id,
                    document_id=rd.document_id,
                    document_version_id=rd.document_version_id,
                    rank=None,
                    reason=f"[merged from result {child_id}] {rd.reason or ''}".strip(),
                    added_by_agent=rd.added_by_agent,
                )
            )
            added += 1
        per_child[str(child_id)] = {"documents": len(rows), "added": added}
        child.parent_result_id = parent_id

    next_seq = await _next_trace_sequence(session, parent_id)
    session.add(
        ResultTrace(
            result_id=parent_id,
            sequence=next_seq,
            agent=AUTO_TRACE_AGENT,
            action="merge_results",
            parameters={"child_result_ids": [str(c) for c in child_result_ids]},
            outcome={
                "per_child": per_child,
                "total_documents": len(existing),
            },
            occurred_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()
    _ = parent
    return {
        "parent_result_id": parent_id,
        "total_documents": len(existing),
        "per_child": per_child,
        "trace_sequence": next_seq,
    }


async def remove_files_from_result(
    session: AsyncSession,
    caller: CallerIdentity,
    result_id: uuid.UUID,
    document_ids: list[uuid.UUID],
    reason: str | None,
) -> tuple[int, int]:
    """Returns (remaining_doc_count, trace_sequence)."""
    result = await _load_draft_result(session, caller, result_id)
    if document_ids:
        await session.execute(
            delete(ResultDocument)
            .where(ResultDocument.result_id == result_id)
            .where(ResultDocument.document_id.in_(document_ids))
        )

    remaining = int(
        (
            await session.execute(
                select(func.count())
                .select_from(ResultDocument)
                .where(ResultDocument.result_id == result_id)
            )
        ).scalar_one()
    )

    next_seq = await _next_trace_sequence(session, result_id)
    session.add(
        ResultTrace(
            result_id=result_id,
            sequence=next_seq,
            agent=AUTO_TRACE_AGENT,
            action="remove_files_from_result",
            parameters={
                "document_ids": [str(d) for d in document_ids],
                "reason": reason,
            },
            outcome={"remaining_doc_count": remaining},
            occurred_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()
    _ = result  # touched for the draft guard
    return remaining, next_seq


async def clear_result_files(
    session: AsyncSession,
    caller: CallerIdentity,
    result_id: uuid.UUID,
) -> tuple[int, int]:
    """Returns (remaining_doc_count=0, trace_sequence)."""
    result = await _load_draft_result(session, caller, result_id)
    await session.execute(
        delete(ResultDocument).where(ResultDocument.result_id == result_id)
    )

    next_seq = await _next_trace_sequence(session, result_id)
    session.add(
        ResultTrace(
            result_id=result_id,
            sequence=next_seq,
            agent=AUTO_TRACE_AGENT,
            action="clear_result_files",
            parameters={},
            outcome={"remaining_doc_count": 0},
            occurred_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()
    _ = result
    return 0, next_seq


# ───────────────────────────── introspect against persisted filter ─────────────────────────────


async def list_candidates_by_result(
    session: AsyncSession,
    caller: CallerIdentity,
    result_id: uuid.UUID,
    limit: int,
    offset: int,
    overlay: GroveFilter | None,
    order_by: str = "filename",
    direction: str = "asc",
    include_toc: bool = False,
) -> dict[str, Any]:
    """Return a page of documents matching the result's persisted filter.

    Enumerates what's actually in the candidate set so the agent can inspect
    individual rows before deciding to `add_files_to_result`. Visibility-scoped
    via the same `apply_grove_filter` machinery introspect uses.

    Sort options for `order_by`:
      - "filename" | "created_at" | "updated_at" | "classification_confidence"
      - "property:<name>" — sort by the document-class property's stored value.
        Text-cast ordering, which works for ISO dates and exact strings.
        Documents without a value for the property come last in asc, first
        in desc (PostgreSQL default NULL ordering).

    Returns a dict `{rows, has_more, next_offset}`. The agent should treat the
    response as paginated and call again with `next_offset` until `has_more`
    is false. Total result-set size can be very large; the page limit caps each
    call so the tool result fits.
    """
    from sqlalchemy import asc as sa_asc, desc as sa_desc
    from app.models import Document, DocumentClassProperty, PropertyValue
    from app.services.introspect import apply_grove_filter
    from app.services.visibility import visible_clause

    result = await load_visible_result(session, caller, result_id, for_write=False)

    stored = GroveFilter(**(result.filter or {}))
    effective = stored if overlay is None else _merge_overlay(stored, overlay)
    read_all = await caller.has_permission("grove.documents.read:all")
    cols: list[Any] = [
        Document.id,
        Document.filename,
        Document.summary,
        Document.document_class_id,
        Document.classification_confidence,
    ]
    if include_toc:
        cols.append(Document.toc)
    stmt = select(*cols).where(visible_clause(Document, caller, read_all=read_all))
    stmt = apply_grove_filter(stmt, effective)

    dir_fn = sa_desc if direction.lower() == "desc" else sa_asc
    if order_by.startswith("property:"):
        prop_name = order_by[len("property:"):]
        if effective.document_class_id is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "property-based ordering requires a document_class_id on the filter",
            )
        prop_id_subq = (
            select(DocumentClassProperty.id)
            .where(DocumentClassProperty.document_class_id == effective.document_class_id)
            .where(DocumentClassProperty.name == prop_name)
            .scalar_subquery()
        )
        order_value_subq = (
            select(PropertyValue.value["_"].astext)
            .where(PropertyValue.document_id == Document.id)
            .where(PropertyValue.property_id == prop_id_subq)
            .limit(1)
            .scalar_subquery()
        )
        stmt = stmt.order_by(dir_fn(order_value_subq), Document.filename)
    elif order_by == "created_at":
        stmt = stmt.order_by(dir_fn(Document.created_at), Document.filename)
    elif order_by == "updated_at":
        stmt = stmt.order_by(dir_fn(Document.updated_at), Document.filename)
    elif order_by == "classification_confidence":
        stmt = stmt.order_by(dir_fn(Document.classification_confidence), Document.filename)
    else:  # "filename" default
        stmt = stmt.order_by(dir_fn(Document.filename))

    # Fetch limit+1 to know if there's a next page without a separate count.
    rows = (await session.execute(stmt.limit(limit + 1).offset(offset))).all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    out: list[dict[str, Any]] = []
    for r in rows:
        entry = {
            "id": str(r.id),
            "filename": r.filename,
            "summary": r.summary,
            "document_class_id": str(r.document_class_id) if r.document_class_id else None,
            "classification_confidence": r.classification_confidence,
        }
        if include_toc:
            entry["toc"] = r.toc
        out.append(entry)
    return {
        "rows": out,
        "has_more": has_more,
        "next_offset": offset + len(out) if has_more else None,
    }


async def introspect_by_result(
    session: AsyncSession,
    caller: CallerIdentity,
    result_id: uuid.UUID,
    fields: list[str] | None,
    top_k: int,
    overlay: GroveFilter | None,
) -> IntrospectOut:
    """Read the persisted filter (optionally with an overlay merged on top
    for preview semantics) and return introspect distributions. Read-only —
    no mutation, no trace.

    Overlay merging: any non-default field on the overlay takes precedence
    over the stored value. Lists in the overlay extend (not replace) the
    stored lists. Useful for the agent to probe "what would this clause do?"
    without committing it.
    """
    result = await load_visible_result(session, caller, result_id, for_write=False)

    stored = GroveFilter(**(result.filter or {}))
    effective = stored if overlay is None else _merge_overlay(stored, overlay)
    return await introspect_with_filter(session, caller, effective, fields, top_k)


def _merge_overlay(base: GroveFilter, overlay: GroveFilter) -> GroveFilter:
    """Overlay extends base. Scalars on overlay replace; lists extend."""
    merged_field_filters = list(base.field_filters)
    for ff in overlay.field_filters:
        merged_field_filters = _without_field(merged_field_filters, ff.field) + [ff]
    merged_regex_filters = list(base.regex_filters)
    for rf in overlay.regex_filters:
        merged_regex_filters = _without_regex_field(merged_regex_filters, rf.field) + [rf]
    return GroveFilter(
        document_class_id=overlay.document_class_id or base.document_class_id,
        field_filters=merged_field_filters,
        regex_filters=merged_regex_filters,
        text_search=overlay.text_search or base.text_search,
        dossier_id=overlay.dossier_id or base.dossier_id,
        dossier_class_id=overlay.dossier_class_id or base.dossier_class_id,
        explicit_excludes=_dedup_extend(
            list(base.explicit_excludes), list(overlay.explicit_excludes)
        ),
        explicit_includes=_dedup_extend(
            list(base.explicit_includes), list(overlay.explicit_includes)
        ),
    )
