"""Owner+roles visibility filter — see ARCHITECTURE.md §3.3."""

from __future__ import annotations

from sqlalchemy import or_
from sqlalchemy.sql.elements import ColumnElement

from app.auth import CallerIdentity


def visible_clause(model, caller: CallerIdentity, read_all: bool) -> ColumnElement[bool]:
    if read_all or caller.is_admin:
        return or_(model.id == model.id)  # always-true
    clauses = [model.owner_id == caller.user_id]
    if caller.roles:
        clauses.append(model.roles.overlap(caller.roles))
    return or_(*clauses)
