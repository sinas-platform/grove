"""Discovery runner — bulk config-suggestion pipeline.

Lifecycle:
  pending → scanning → consolidating → completed | failed | cancelled

Stages:
  1. Scan: invoke `grove/discovery-agent` per doc, with `kind` in the input.
     Agent emits raw `discovery_candidate` rows via the connector.
  2. Consolidate: once all per-doc units are done, invoke
     `grove/discovery-consolidator-agent` once with the run_id. It reads all
     candidates for the run, clusters duplicates, and emits `config_proposal`
     rows via the connector.

The runner reuses the same single-process worker pattern as ingestion_runner.
"""

from __future__ import annotations

import asyncio
import logging
import random
import uuid
from datetime import datetime, timezone
from typing import Any

from sinas import SinasClient
from sinas.exceptions import SinasAPIError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import AsyncSessionLocal
from app.models import (
    ConfigProposal,
    DiscoveryCandidate,
    DiscoveryRun,
    DiscoveryRunUnit,
    Document,
)
from app.schemas.ingestion import RunFilter
from app.services.sinas import get_admin_client

log = logging.getLogger(__name__)


# Per-kind agent assignment.
KIND_TO_AGENT = {
    "document_class": ("grove", "discovery-agent"),
    "entity_type": ("grove", "discovery-agent"),
    "relationship_definition": ("grove", "discovery-agent"),
    "dossier_class": ("grove", "discovery-agent"),
    "document_class_property": ("grove", "discovery-agent"),
}

CONSOLIDATOR_AGENT = ("grove", "discovery-consolidator-agent")


async def _select_documents(
    session: AsyncSession,
    f: RunFilter,
    parent_class_id: uuid.UUID | None,
    sample_size: int | None,
) -> list[uuid.UUID]:
    stmt = select(Document.id)
    if f.document_ids:
        stmt = stmt.where(Document.id.in_(f.document_ids))
    if f.document_class_ids:
        stmt = stmt.where(Document.document_class_id.in_(f.document_class_ids))
    if f.created_since:
        stmt = stmt.where(Document.created_at >= f.created_since)
    if f.created_until:
        stmt = stmt.where(Document.created_at <= f.created_until)
    # Property discovery is scoped to a parent class.
    if parent_class_id is not None:
        stmt = stmt.where(Document.document_class_id == parent_class_id)
    rows = list((await session.execute(stmt)).scalars().all())

    if sample_size is not None and len(rows) > sample_size:
        rng = random.Random(42)
        rows = rng.sample(rows, sample_size)
    return rows


async def expand_filter(
    session: AsyncSession,
    f: RunFilter,
    parent_class_id: uuid.UUID | None,
    sample_size: int | None,
) -> tuple[int, bool]:
    """Returns (count_to_scan, was_sampled)."""
    docs = await _select_documents(session, f, parent_class_id, sample_size)
    return len(docs), sample_size is not None and len(docs) >= sample_size


async def materialize_run(session: AsyncSession, run: DiscoveryRun) -> int:
    f = RunFilter(**(run.filter or {}))
    doc_ids = await _select_documents(
        session, f, run.parent_class_id, run.sample_size
    )
    now = datetime.now(timezone.utc)
    units = [
        DiscoveryRunUnit(
            run_id=run.id,
            document_id=doc_id,
            status="pending",
            attempts=0,
            created_at=now,
        )
        for doc_id in doc_ids
    ]
    session.add_all(units)
    return len(units)


def _invoke_discovery_sync(
    client: SinasClient,
    namespace: str,
    agent_name: str,
    *,
    run_id: uuid.UUID,
    kind: str,
    mode: str,
    document_id: uuid.UUID,
    parent_class_id: uuid.UUID | None,
) -> dict[str, Any]:
    return client.chats.invoke(
        namespace=namespace,
        agent_name=agent_name,
        message=(
            f"Discovery pass — kind '{kind}' (mode '{mode}'). Read document "
            f"{document_id} and submit any candidate {kind}s you find via "
            "submit_discovery_candidate. Be conservative with confidence."
        ),
        input={
            "run_id": str(run_id),
            "kind": kind,
            "mode": mode,
            "document_id": str(document_id),
            **(
                {"parent_class_id": str(parent_class_id)}
                if parent_class_id is not None
                else {}
            ),
        },
    )


def _invoke_consolidator_sync(
    client: SinasClient, run_id: uuid.UUID, kind: str
) -> dict[str, Any]:
    namespace, agent_name = CONSOLIDATOR_AGENT
    return client.chats.invoke(
        namespace=namespace,
        agent_name=agent_name,
        message=(
            f"Consolidate the discovery candidates for run {run_id} (kind '{kind}'). "
            "Cluster semantic duplicates, choose canonical names, and submit the "
            "deduplicated proposals via submit_consolidated_proposal. Each proposal "
            "must list the supporting candidate ids that were folded into it."
        ),
        input={"run_id": str(run_id), "kind": kind},
    )


async def _process_unit(
    unit: DiscoveryRunUnit, run: DiscoveryRun, client: SinasClient
) -> tuple[bool, str | None, str | None]:
    namespace, agent_name = KIND_TO_AGENT[run.kind]
    try:
        result = await asyncio.to_thread(
            _invoke_discovery_sync,
            client,
            namespace,
            agent_name,
            run_id=run.id,
            kind=run.kind,
            mode=run.mode,
            document_id=unit.document_id,
            parent_class_id=run.parent_class_id,
        )
    except SinasAPIError as exc:
        return False, f"sinas: {exc}", None
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}", None
    chat_id = result.get("chat_id") if isinstance(result, dict) else None
    return True, None, chat_id


async def _claim_next_pending_unit(
    session: AsyncSession, run_id: uuid.UUID
) -> DiscoveryRunUnit | None:
    row = (
        await session.execute(
            select(DiscoveryRunUnit)
            .where(DiscoveryRunUnit.run_id == run_id)
            .where(DiscoveryRunUnit.status == "pending")
            .order_by(DiscoveryRunUnit.created_at)
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


async def _run_scan_phase(run_id: uuid.UUID) -> None:
    settings = get_settings()
    sem = asyncio.Semaphore(settings.grove_rerun_concurrency)
    client = get_admin_client()

    # Cache the run shape — the units all share kind/mode/parent.
    async with AsyncSessionLocal() as session:
        run = await session.get(DiscoveryRun, run_id)
        if run is None:
            return

    async def worker() -> None:
        while True:
            async with AsyncSessionLocal() as session:
                unit = await _claim_next_pending_unit(session, run_id)
            if unit is None:
                return
            async with sem:
                ok, err, chat_id = await _process_unit(unit, run, client)
            async with AsyncSessionLocal() as session:
                u = await session.get(DiscoveryRunUnit, unit.id)
                if u is None:
                    continue
                u.status = "succeeded" if ok else "failed"
                u.error = err
                u.chat_id = chat_id
                u.completed_at = datetime.now(timezone.utc)
                r = await session.get(DiscoveryRun, run_id)
                if r is not None:
                    r.scanned_docs += 1
                    if not ok:
                        r.failed_docs += 1
                await session.commit()

    workers = [
        asyncio.create_task(worker()) for _ in range(settings.grove_rerun_concurrency)
    ]
    await asyncio.gather(*workers, return_exceptions=False)


async def _run_consolidate_phase(run_id: uuid.UUID) -> None:
    client = get_admin_client()
    async with AsyncSessionLocal() as session:
        run = await session.get(DiscoveryRun, run_id)
        if run is None:
            return
        run.status = "consolidating"
        run.consolidating_at = datetime.now(timezone.utc)
        await session.commit()
        kind = run.kind
    try:
        await asyncio.to_thread(_invoke_consolidator_sync, client, run_id, kind)
    except Exception as exc:  # noqa: BLE001
        async with AsyncSessionLocal() as session:
            run = await session.get(DiscoveryRun, run_id)
            if run is not None:
                run.status = "failed"
                run.error = f"consolidation: {type(exc).__name__}: {exc}"
                run.completed_at = datetime.now(timezone.utc)
                await session.commit()
        raise


async def _execute_run(run_id: uuid.UUID) -> None:
    async with AsyncSessionLocal() as session:
        run = await session.get(DiscoveryRun, run_id)
        if run is None:
            return
        if run.status != "scanning":
            run.status = "scanning"
            run.started_at = datetime.now(timezone.utc)
            await session.commit()

    # Phase 1: scan
    await _run_scan_phase(run_id)

    # Phase 2: consolidate (single agent invocation)
    await _run_consolidate_phase(run_id)

    # Mark completed
    async with AsyncSessionLocal() as session:
        run = await session.get(DiscoveryRun, run_id)
        if run is not None:
            cand_count = (
                await session.execute(
                    select(func.count())
                    .select_from(DiscoveryCandidate)
                    .where(DiscoveryCandidate.run_id == run_id)
                )
            ).scalar_one()
            run.candidate_count = int(cand_count)
            prop_count = (
                await session.execute(
                    select(func.count())
                    .select_from(ConfigProposal)
                    .where(ConfigProposal.discovery_run_id == run_id)
                )
            ).scalar_one()
            run.proposal_count = int(prop_count)
            run.status = "completed"
            run.completed_at = datetime.now(timezone.utc)
            await session.commit()


# ────────────────────── background worker loop ──────────────────────
_worker_task: asyncio.Task | None = None
_shutdown = asyncio.Event()


async def _claim_next_run() -> uuid.UUID | None:
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(DiscoveryRun)
                .where(DiscoveryRun.status == "pending")
                .order_by(DiscoveryRun.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        row.status = "scanning"
        row.started_at = datetime.now(timezone.utc)
        await session.commit()
        return row.id


async def _resume_running_runs() -> list[uuid.UUID]:
    async with AsyncSessionLocal() as session:
        runs = (
            await session.execute(
                select(DiscoveryRun.id).where(
                    DiscoveryRun.status.in_(["scanning", "consolidating"])
                )
            )
        ).scalars().all()
        for run_id in runs:
            await session.execute(
                DiscoveryRunUnit.__table__.update()
                .where(DiscoveryRunUnit.run_id == run_id)
                .where(DiscoveryRunUnit.status == "running")
                .values(status="pending")
            )
        await session.commit()
        return list(runs)


async def _worker_loop() -> None:
    log.info("discovery-runner worker started")
    resumed = await _resume_running_runs()
    for run_id in resumed:
        log.info("resuming discovery run %s", run_id)
        try:
            await _execute_run(run_id)
        except Exception as exc:  # noqa: BLE001
            log.exception("resumed discovery run %s crashed: %s", run_id, exc)

    while not _shutdown.is_set():
        run_id = await _claim_next_run()
        if run_id is None:
            try:
                await asyncio.wait_for(_shutdown.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
            continue
        log.info("starting discovery run %s", run_id)
        try:
            await _execute_run(run_id)
        except Exception as exc:  # noqa: BLE001
            log.exception("discovery run %s crashed: %s", run_id, exc)
            async with AsyncSessionLocal() as session:
                run = await session.get(DiscoveryRun, run_id)
                if run is not None and run.status not in ("completed", "failed"):
                    run.status = "failed"
                    run.error = f"{type(exc).__name__}: {exc}"
                    run.completed_at = datetime.now(timezone.utc)
                    await session.commit()
    log.info("discovery-runner worker stopped")


def start_worker() -> asyncio.Task:
    global _worker_task
    if _worker_task is None or _worker_task.done():
        _shutdown.clear()
        _worker_task = asyncio.create_task(_worker_loop(), name="discovery-runner")
    return _worker_task


async def stop_worker() -> None:
    _shutdown.set()
    if _worker_task is not None:
        try:
            await asyncio.wait_for(_worker_task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            _worker_task.cancel()
