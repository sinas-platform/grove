"""Bulk reprocess worker.

The worker loop polls `ingestion_run` for runs in 'pending' state, claims one,
expands its filter into `ingestion_run_unit` rows (one per doc × stage), and
processes them with a configurable concurrency cap. On Grove restart, runs
in the 'running' state with pending units are resumed.

A unit is one (document, stage) — re-running a stage re-invokes the
corresponding sub-agent. Stale auto-extracted artifacts are wiped before each
agent runs (so we don't accumulate duplicate entity mentions / property
values across reruns); manually-authored data is preserved.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sinas import SinasClient
from sinas.exceptions import SinasAPIError
from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import AsyncSessionLocal
from app.models import (
    Document,
    EntityMention,
    IngestionRun,
    IngestionRunUnit,
    PropertyValue,
)
from app.schemas.ingestion import RunFilter
from app.services.sinas import get_admin_client

log = logging.getLogger(__name__)


# Per-stage config: which Sinas agent runs the work, and how to wipe stale
# auto-extracted artifacts before re-running so we don't duplicate.
STAGES: dict[str, dict[str, Any]] = {
    "classifier": {
        "agent": ("grove", "classifier-agent"),
        "label": "Classification",
    },
    "summarizer": {
        "agent": ("grove", "summarizer-agent"),
        "label": "Summarization",
    },
    "property_extractor": {
        "agent": ("grove", "property-extractor-agent"),
        "label": "Property extraction",
    },
    "entity_extractor": {
        "agent": ("grove", "entity-extractor-agent"),
        "label": "Entity extraction",
    },
    "relationship_extractor": {
        "agent": ("grove", "relationship-extractor-agent"),
        "label": "Relationship extraction",
    },
    "dossier_assigner": {
        "agent": ("grove", "dossier-assigner-agent"),
        "label": "Dossier assignment",
    },
}


async def _wipe_for_stage(session: AsyncSession, document_id: uuid.UUID, stage: str) -> None:
    """Delete stale auto-extracted artifacts so a rerun doesn't duplicate.

    Manually-authored / locked entries are preserved.
    """
    if stage == "property_extractor":
        await session.execute(
            delete(PropertyValue)
            .where(PropertyValue.document_id == document_id)
            .where(PropertyValue.method == "auto")
            .where(PropertyValue.locked.is_(False))
        )
    elif stage == "entity_extractor":
        await session.execute(
            delete(EntityMention).where(EntityMention.document_id == document_id)
        )
    # classifier/summarizer overwrite document fields directly — no wipe needed.
    # relationship_extractor / dossier_assigner: skip wipe in v1 (manual entries
    # may exist; risk of duplicates is acceptable for now).


async def _select_documents(session: AsyncSession, f: RunFilter) -> list[uuid.UUID]:
    stmt = select(Document.id)
    if f.document_ids:
        stmt = stmt.where(Document.id.in_(f.document_ids))
    if f.document_class_ids:
        stmt = stmt.where(Document.document_class_id.in_(f.document_class_ids))
    if f.created_since:
        stmt = stmt.where(Document.created_at >= f.created_since)
    if f.created_until:
        stmt = stmt.where(Document.created_at <= f.created_until)
    rows = (await session.execute(stmt)).scalars().all()
    return list(rows)


async def expand_filter(session: AsyncSession, f: RunFilter) -> list[uuid.UUID]:
    """Used by the API to preview document counts before committing a run."""
    return await _select_documents(session, f)


async def materialize_run(
    session: AsyncSession,
    run: IngestionRun,
) -> int:
    """Insert IngestionRunUnit rows for every (doc, stage) pair. Returns count."""
    f = RunFilter(**(run.filter or {}))
    doc_ids = await _select_documents(session, f)
    now = datetime.now(timezone.utc)
    units = [
        IngestionRunUnit(
            run_id=run.id,
            document_id=doc_id,
            stage=stage,
            status="pending",
            attempts=0,
            created_at=now,
        )
        for doc_id in doc_ids
        for stage in run.stages
    ]
    session.add_all(units)
    return len(units)


def _invoke_agent_sync(
    client: SinasClient, namespace: str, agent_name: str, document_id: uuid.UUID, stage: str
) -> dict[str, Any]:
    """Sync wrapper around the SDK invoke; called via asyncio.to_thread."""
    return client.chats.invoke(
        namespace=namespace,
        agent_name=agent_name,
        message=(
            f"Reprocess document {document_id} for stage '{stage}'. "
            "Run only this stage; do not invoke other agents."
        ),
        input={"document_id": str(document_id), "stage": stage},
    )


async def _process_unit(
    unit: IngestionRunUnit, client: SinasClient
) -> tuple[bool, str | None, str | None]:
    """Process a single (doc, stage) unit. Returns (ok, error, chat_id)."""
    stage_cfg = STAGES.get(unit.stage)
    if stage_cfg is None:
        return False, f"unknown stage '{unit.stage}'", None
    namespace, agent_name = stage_cfg["agent"]

    # Wipe stale auto-extracted artifacts in its own transaction so the agent
    # writes into a clean slate.
    async with AsyncSessionLocal() as session:
        try:
            await _wipe_for_stage(session, unit.document_id, unit.stage)
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            return False, f"wipe failed: {type(exc).__name__}: {exc}", None

    # Invoke the agent. The SDK is sync; offload to a thread.
    try:
        result = await asyncio.to_thread(
            _invoke_agent_sync, client, namespace, agent_name, unit.document_id, unit.stage
        )
    except SinasAPIError as exc:
        return False, f"sinas: {exc}", None
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}", None
    chat_id = result.get("chat_id") if isinstance(result, dict) else None
    return True, None, chat_id


async def _claim_next_pending_unit(
    session: AsyncSession, run_id: uuid.UUID
) -> IngestionRunUnit | None:
    """Pick the next pending unit for a run, mark it running atomically."""
    # Postgres SKIP LOCKED would be ideal here, but for simplicity we just
    # rely on the worker being single-process. If we ever scale to multiple
    # Grove backends, switch to SELECT … FOR UPDATE SKIP LOCKED.
    row = (
        await session.execute(
            select(IngestionRunUnit)
            .where(IngestionRunUnit.run_id == run_id)
            .where(IngestionRunUnit.status == "pending")
            .order_by(IngestionRunUnit.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    row.status = "running"
    row.started_at = datetime.now(timezone.utc)
    row.attempts += 1
    await session.commit()
    return row


async def _execute_run(run_id: uuid.UUID) -> None:
    """Process all pending units for a run with bounded concurrency."""
    settings = get_settings()
    sem = asyncio.Semaphore(settings.grove_rerun_concurrency)
    client = get_admin_client()

    async def worker() -> None:
        while True:
            async with AsyncSessionLocal() as session:
                unit = await _claim_next_pending_unit(session, run_id)
            if unit is None:
                return
            async with sem:
                ok, err, chat_id = await _process_unit(unit, client)
            async with AsyncSessionLocal() as session:
                u = await session.get(IngestionRunUnit, unit.id)
                if u is None:
                    continue
                u.status = "succeeded" if ok else "failed"
                u.error = err
                u.chat_id = chat_id
                u.completed_at = datetime.now(timezone.utc)
                # Increment run-level counters.
                run = await session.get(IngestionRun, run_id)
                if run is not None:
                    run.done_units += 1
                    if not ok:
                        run.failed_units += 1
                await session.commit()

    # Spawn N workers up to the concurrency cap.
    workers = [asyncio.create_task(worker()) for _ in range(settings.grove_rerun_concurrency)]
    try:
        await asyncio.gather(*workers, return_exceptions=False)
    finally:
        # Mark run completed.
        async with AsyncSessionLocal() as session:
            run = await session.get(IngestionRun, run_id)
            if run is not None and run.status == "running":
                pending = (
                    await session.execute(
                        select(func.count())
                        .select_from(IngestionRunUnit)
                        .where(IngestionRunUnit.run_id == run_id)
                        .where(IngestionRunUnit.status.in_(["pending", "running"]))
                    )
                ).scalar_one()
                if pending == 0:
                    run.status = "completed" if run.failed_units == 0 else "completed"
                    run.completed_at = datetime.now(timezone.utc)
                    await session.commit()


# ────────────────────── background worker loop ──────────────────────
_worker_task: asyncio.Task | None = None
_shutdown = asyncio.Event()


async def _claim_next_run() -> uuid.UUID | None:
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(IngestionRun)
                .where(IngestionRun.status == "pending")
                .order_by(IngestionRun.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        row.status = "running"
        row.started_at = datetime.now(timezone.utc)
        await session.commit()
        return row.id


async def _resume_running_runs() -> list[uuid.UUID]:
    """On startup, find runs that were 'running' but the worker died.
    Reset their in-flight units to pending so they pick back up."""
    async with AsyncSessionLocal() as session:
        runs = (
            await session.execute(
                select(IngestionRun.id).where(IngestionRun.status == "running")
            )
        ).scalars().all()
        for run_id in runs:
            await session.execute(
                IngestionRunUnit.__table__.update()
                .where(IngestionRunUnit.run_id == run_id)
                .where(IngestionRunUnit.status == "running")
                .values(status="pending")
            )
        await session.commit()
        return list(runs)


async def _worker_loop() -> None:
    log.info("ingestion-runner worker started")
    # Pick up runs that were mid-flight when the previous process died.
    resumed = await _resume_running_runs()
    for run_id in resumed:
        log.info("resuming ingestion run %s", run_id)
        try:
            await _execute_run(run_id)
        except Exception as exc:  # noqa: BLE001
            log.exception("resumed run %s crashed: %s", run_id, exc)

    while not _shutdown.is_set():
        run_id = await _claim_next_run()
        if run_id is None:
            try:
                await asyncio.wait_for(_shutdown.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
            continue
        log.info("starting ingestion run %s", run_id)
        try:
            await _execute_run(run_id)
        except Exception as exc:  # noqa: BLE001
            log.exception("run %s crashed: %s", run_id, exc)
            async with AsyncSessionLocal() as session:
                run = await session.get(IngestionRun, run_id)
                if run is not None:
                    run.status = "failed"
                    run.error = f"{type(exc).__name__}: {exc}"
                    run.completed_at = datetime.now(timezone.utc)
                    await session.commit()
    log.info("ingestion-runner worker stopped")


def start_worker() -> asyncio.Task:
    global _worker_task
    if _worker_task is None or _worker_task.done():
        _shutdown.clear()
        _worker_task = asyncio.create_task(_worker_loop(), name="ingestion-runner")
    return _worker_task


async def stop_worker() -> None:
    _shutdown.set()
    if _worker_task is not None:
        try:
            await asyncio.wait_for(_worker_task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            _worker_task.cancel()
