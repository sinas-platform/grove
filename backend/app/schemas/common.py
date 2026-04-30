from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class TimestampedOut(ORMModel):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class OwnedOut(TimestampedOut):
    owner_id: uuid.UUID
    roles: list[str] = Field(default_factory=list)


class Span(BaseModel):
    """A pointer into a document version: line range or character offsets."""

    line_from: int | None = None
    line_to: int | None = None
    char_from: int | None = None
    char_to: int | None = None
    note: str | None = None


class TraceOut(ORMModel):
    id: uuid.UUID
    sequence: int
    agent: str
    action: str
    parameters: dict[str, Any] | None = None
    outcome: dict[str, Any] | None = None
    occurred_at: datetime
