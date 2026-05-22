from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel
from app.schemas.ingestion import RunFilter

DiscoveryKind = Literal[
    "document_class",
    "entity_type",
    "relationship_definition",
    "dossier_class",
    "document_class_property",
]

DiscoveryMode = Literal["greenfield", "incremental"]


# ─────────────────────── runs ───────────────────────
class DiscoveryRunCreateIn(BaseModel):
    kind: DiscoveryKind
    mode: DiscoveryMode = "incremental"
    filter: RunFilter = Field(default_factory=RunFilter)
    sample_size: int | None = Field(
        default=None,
        description="If set, scan a random sample of this many docs instead of all matches.",
    )
    parent_class_id: uuid.UUID | None = Field(
        default=None,
        description="Required when kind=document_class_property — which class to discover properties for.",
    )
    dry_run: bool = False


class DiscoveryRunCreateOut(BaseModel):
    run_id: uuid.UUID | None = None
    document_count: int
    sampled: bool
    status: str  # "started" | "would_start"


# ─────────────────────── front-matter (deterministic) ───────────────────────
class FrontMatterSuggestIn(BaseModel):
    filter: RunFilter = Field(default_factory=RunFilter)
    sample_size: int | None = Field(
        default=None,
        description="If set, scan a random sample of this many docs. Front-matter parsing is cheap, so leaving this unset to scan the full corpus is reasonable.",
    )
    parent_class_id: uuid.UUID | None = Field(
        default=None,
        description="If set, property proposals are bound to this class. Without it, property proposals lack a target class and can only be approved after manual edit.",
    )


class FrontMatterSuggestOut(BaseModel):
    run_id: uuid.UUID
    document_count: int
    documents_with_front_matter: int
    candidate_count: int
    proposal_count: int


# ─────────────────────── unified suggest (FM + LLM) ───────────────────────
class SuggestIn(BaseModel):
    kind: DiscoveryKind
    mode: DiscoveryMode = "incremental"
    filter: RunFilter = Field(default_factory=RunFilter)
    sample_size: int | None = Field(
        default=None,
        description="Sample size for the LLM discovery pass. Front-matter scan is always full (it's free).",
    )
    parent_class_id: uuid.UUID | None = Field(
        default=None,
        description="Required when kind=document_class_property — which class to discover properties for.",
    )
    include_front_matter: bool = Field(
        default=True,
        description="If true, run the deterministic front-matter scan first. Skips when kind has no front-matter mapping (relationship_definition, dossier_class).",
    )
    skip_llm: bool = Field(
        default=False,
        description="If true, only the front-matter pass runs. Useful when the corpus has rich YAML headers and LLM cost isn't justified.",
    )


class SuggestOut(BaseModel):
    front_matter_run_id: uuid.UUID | None
    front_matter_proposal_count: int
    front_matter_documents_with_fm: int
    discovery_run_id: uuid.UUID | None
    discovery_document_count: int
    discovery_sampled: bool


class DiscoveryRunOut(ORMModel):
    id: uuid.UUID
    kind: str
    status: str
    mode: str
    filter: dict
    parent_class_id: uuid.UUID | None = None
    sample_size: int | None = None
    total_docs: int
    scanned_docs: int
    failed_docs: int
    candidate_count: int
    proposal_count: int
    error: str | None = None
    started_by: uuid.UUID
    created_at: datetime
    started_at: datetime | None = None
    consolidating_at: datetime | None = None
    completed_at: datetime | None = None


# ─────────────────────── candidate writes (called by Sinas agent) ───────────────────────
class DiscoveryCandidateIn(BaseModel):
    run_id: uuid.UUID
    kind: DiscoveryKind
    payload: dict[str, Any]
    evidence_document_id: uuid.UUID | None = None
    evidence_span: dict[str, Any] | None = None
    confidence: float | None = None


# ─────────────────────── consolidated proposal writes ───────────────────────
class ConsolidatedProposalIn(BaseModel):
    run_id: uuid.UUID
    kind: DiscoveryKind
    payload: dict[str, Any]
    supporting_candidate_ids: list[uuid.UUID]


# ─────────────────────── proposal review ───────────────────────
class ConfigProposalOut(ORMModel):
    id: uuid.UUID
    kind: str
    payload: dict
    status: str
    supporting_candidate_ids: list[str]
    discovery_run_id: uuid.UUID | None = None
    created_resource_id: uuid.UUID | None = None
    merged_into_id: uuid.UUID | None = None
    notes: str | None = None
    reviewed_at: datetime | None = None
    reviewed_by: uuid.UUID | None = None
    created_at: datetime


class ProposalEditIn(BaseModel):
    payload: dict[str, Any]


class ProposalMergeIn(BaseModel):
    target_id: uuid.UUID  # existing config row this proposal is being folded into
