"""Server-supervised question pipeline (the query-side ingestion_runner).

The choreography of a question — dispatch sub-searches, wait, merge, brief
synthesis, validate, publish — lives HERE, in code, with per-stage state
checkpointed on the QueryRun row. Agents are consulted only for judgment:

  decompose   one forced-JSON call (search-orchestrator agent, single turn)
  search      grove/deep-search-agent chats — the agentic retrieval core
  draft       grove/synthesis-agent chat, scoped to DRAFTING ONLY
  verdicts    the stateless evidence-check fan-out (services/faithfulness)

Supervision rules: stage completion is observed in the DATABASE, never on a
held HTTP connection; a silent chat (no new messages, no artifact progress)
is nudged at most MAX_NUDGES times, then a search is re-dispatched once and
anything else fails the run explicitly. Every transition lands in
QueryRun.telemetry. A failed run can be resumed: completed stages short-
circuit off the persisted state.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select

from app.auth import CallerIdentity
from app.config import get_settings
from app.db import AsyncSessionLocal
from app.models import AnswerClaim, ClaimEvidence, Document, DocumentClass, Result, ResultDocument
from app.models.query import QueryRun

POLL_S = 12
SEARCH_TIMEOUT_S = 25 * 60
DRAFT_TIMEOUT_S = 20 * 60
IDLE_DEAD_S = 150
MAX_NUDGES = 2
MAX_VALIDATE_ROUNDS = 4
REMEDIATION_WINDOW_S = 8 * 60
MIN_CLAIMS = 6
# effort → maximum sub-query fan-out. The bound is enforced here (truncation)
# AND stated in the decompose instruction; no magic numbers in agent prose.
EFFORT_FANOUT = {"low": 1, "medium": 2, "high": 3}

_log = __import__("logging").getLogger("grove.query_runner")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso() -> str:
    return _now().isoformat()


class _Sinas:
    """Minimal async Sinas client for chat supervision."""

    def __init__(self) -> None:
        s = get_settings()
        self.base = s.sinas_url
        self.headers = {"Authorization": f"Bearer {s.sinas_api_key}"}

    async def chat_create(self, agent: str, title: str) -> str:
        async with httpx.AsyncClient(timeout=60.0) as c:
            r = await c.post(
                f"{self.base}/agents/{agent}/chats",
                headers=self.headers,
                json={"title": title, "keep_alive": True, "job_timeout": 3600},
            )
            r.raise_for_status()
            return r.json()["id"]

    def send_detached(self, chat_id: str, content: str) -> None:
        """Fire the message; observe completion via the DB, never this socket."""

        async def _fire() -> None:
            try:
                async with httpx.AsyncClient(timeout=3600.0) as c:
                    await c.post(
                        f"{self.base}/chats/{chat_id}/messages",
                        headers=self.headers,
                        json={"content": content},
                    )
            except Exception:  # job continues server-side (keep_alive)
                pass

        asyncio.create_task(_fire())

    async def invoke(self, agent: str, message: str) -> str:
        async with httpx.AsyncClient(timeout=600.0) as c:
            r = await c.post(
                f"{self.base}/agents/{agent}/invoke",
                headers=self.headers,
                json={"message": message},
            )
            r.raise_for_status()
            return r.json().get("reply", "") or ""

    async def chat_last_activity(self, chat_id: str) -> datetime | None:
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.get(f"{self.base}/chats/{chat_id}", headers=self.headers)
            if r.status_code != 200:
                return None
            msgs = r.json().get("messages") or []
            if not msgs:
                return None
            ts = msgs[-1].get("created_at")
            try:
                return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            except Exception:
                return None


def _runner_caller(run: QueryRun) -> CallerIdentity:
    s = get_settings()
    return CallerIdentity(
        user_id=run.owner_id,
        roles=list(run.roles or []),
        is_admin=True,  # server-side pipeline acts with operator authority
        sinas_token=s.sinas_api_key,
    )


async def _mark(run_id: uuid.UUID, **fields: Any) -> None:
    async with AsyncSessionLocal() as session:
        run = await session.get(QueryRun, run_id)
        for k, v in fields.items():
            setattr(run, k, v)
        await session.commit()


async def _tele(run_id: uuid.UUID, stage: str, **detail: Any) -> None:
    async with AsyncSessionLocal() as session:
        run = await session.get(QueryRun, run_id)
        t = dict(run.telemetry or {})
        entry = dict(t.get(stage) or {})
        entry.update(detail)
        t[stage] = entry
        run.telemetry = t
        await session.commit()


async def _chat_is_idle(sinas: _Sinas, chat_id: str) -> bool:
    last = await sinas.chat_last_activity(chat_id)
    if last is None:
        return True
    return (_now() - last).total_seconds() > IDLE_DEAD_S


# ── stages ──────────────────────────────────────────────────────────────────


async def _stage_decompose(run_id: uuid.UUID, sinas: _Sinas) -> list[str]:
    async with AsyncSessionLocal() as session:
        run = await session.get(QueryRun, run_id)
        if run.subqueries:
            return list(run.subqueries)
        question = run.question
        max_fanout = EFFORT_FANOUT.get(run.effort, 2)
    await _mark(run_id, status="decomposing")
    await _tele(run_id, "decompose", started=_iso(), max_fanout=max_fanout)
    reply = await sinas.invoke(
        "grove/search-orchestrator",
        "Decompose the following question into independent retrieval sub-queries. "
        f"Use AT MOST {max_fanout} sub-quer{'y' if max_fanout == 1 else 'ies'}; "
        "fewer is better when the question does not demand parallel angles. "
        "Reply with ONLY a JSON array of strings.\n\n"
        f"Question: {question}",
    )
    try:
        cleaned = reply.strip().strip("`")
        cleaned = cleaned.removeprefix("json").strip()
        subs = json.loads(cleaned)
        assert isinstance(subs, list) and subs and all(isinstance(x, str) for x in subs)
    except Exception:
        subs = [question]
    subs = subs[:max_fanout]
    await _mark(run_id, subqueries=subs)
    await _tele(run_id, "decompose", completed=_iso(), subqueries=subs)
    return subs


async def _search_result_for(started_iso: str, subquery: str) -> tuple[str, str] | None:
    frag = subquery[:25]
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(Result.id, Result.status)
                .where(Result.created_at > datetime.fromisoformat(started_iso))
                .where(Result.query.ilike(f"%{frag}%"))
                .order_by(Result.created_at.desc())
                .limit(1)
            )
        ).first()
    return (str(row[0]), row[1]) if row else None


async def _stage_search(run_id: uuid.UUID, sinas: _Sinas) -> list[str]:
    async with AsyncSessionLocal() as session:
        run = await session.get(QueryRun, run_id)
        question, subs = run.question, list(run.subqueries)
        searches: dict[str, dict] = dict(run.searches or {})
        done = {s: m["result_id"] for s, m in searches.items() if m.get("result_id")}
        if len(done) == len(subs):
            return [done[s] for s in subs]
    await _mark(run_id, status="searching")
    await _tele(run_id, "search", started=_iso())

    dispatch_msg = (
        "Run your retrieval workflow for this sub-query and publish the result. "
        "Do not end your turn before publish_result succeeds.\n\n"
        "Sub-query: {sq}\n\nContext — the user's full question: {q}"
    )
    for sq in subs:
        if sq not in searches:
            chat = await sinas.chat_create("grove/deep-search-agent", f"[query-run] {sq[:50]}")
            searches[sq] = {"chat_id": chat, "started": _iso(), "nudges": 0, "redispatched": False}
            sinas.send_detached(chat, dispatch_msg.format(sq=sq, q=question))
    await _mark(run_id, searches=searches)

    deadline = asyncio.get_event_loop().time() + SEARCH_TIMEOUT_S
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(POLL_S)
        changed = False
        for sq, meta in searches.items():
            if meta.get("result_id"):
                continue
            found = await _search_result_for(meta["started"], sq)
            if found and found[1] == "published":
                meta["result_id"] = found[0]
                changed = True
                continue
            if not await _chat_is_idle(sinas, meta["chat_id"]):
                continue
            if meta["nudges"] < MAX_NUDGES:
                meta["nudges"] += 1
                changed = True
                sinas.send_detached(
                    meta["chat_id"],
                    "Continue your retrieval workflow from where you stopped"
                    + (" — your draft result is unpublished; finish validation and publish it."
                       if found else " — create and publish the result.")
                    + " Do not end your turn before publish_result succeeds.",
                )
            elif not meta["redispatched"]:
                meta.update(redispatched=True, nudges=0, started=_iso())
                chat = await sinas.chat_create("grove/deep-search-agent", f"[query-run retry] {sq[:40]}")
                meta["chat_id"] = chat
                sinas.send_detached(chat, dispatch_msg.format(sq=sq, q=question))
                changed = True
            else:
                raise RuntimeError(f"sub-search dead after nudges+retry: {sq[:60]!r}")
        if changed:
            await _mark(run_id, searches=searches)
        if all(m.get("result_id") for m in searches.values()):
            await _tele(run_id, "search", completed=_iso(),
                        results={s: m["result_id"] for s, m in searches.items()})
            return [searches[s]["result_id"] for s in subs]
    raise RuntimeError("search stage timed out")


async def _stage_merge(run_id: uuid.UUID, children: list[str]) -> uuid.UUID:
    from app.services.result_filter import merge_results

    async with AsyncSessionLocal() as session:
        run = await session.get(QueryRun, run_id)
        if run.parent_result_id:
            return run.parent_result_id
        caller = _runner_caller(run)
        question = run.question
        await session.commit()

    await _mark(run_id, status="merging")
    child_ids = [uuid.UUID(c) for c in children]
    async with AsyncSessionLocal() as session:
        if len(child_ids) == 1:
            parent_id = child_ids[0]
        else:
            parent = Result(
                query=question,
                invoked_skill_names=["query-run"],
                owner_id=caller.user_id,
                roles=caller.roles or [],
            )
            session.add(parent)
            await session.commit()
            await session.refresh(parent)
            parent_id = parent.id
            summary = await merge_results(session, caller, parent_id, child_ids)
            await _tele(run_id, "merge", **{k: v for k, v in summary.items() if k != "parent_result_id"})
        # publish via the API layer's logic (coverage metric) is HTTP-only;
        # publishing directly here keeps it in-process:
        row = await session.get(Result, parent_id)
        if row.status != "published":
            row.status = "published"
            row.published_at = _now()
            await session.commit()
    await _mark(run_id, parent_result_id=parent_id)
    return parent_id


async def _stage_discovery(run_id: uuid.UUID, sinas: _Sinas) -> None:
    async with AsyncSessionLocal() as session:
        run = await session.get(QueryRun, run_id)
        if not run.run_discovery or (run.telemetry or {}).get("discovery"):
            return
        parent = run.parent_result_id
    chat = await sinas.chat_create("grove/relationship-discovery-agent", "[query-run] discovery")
    sinas.send_detached(chat, f"Surface relationship proposals for the documents of result {parent}. Write proposals only.")
    await _tele(run_id, "discovery", fired=_iso(), chat_id=chat)


async def _doc_manifest(parent_id: uuid.UUID) -> str:
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(
                    Document.filename,
                    DocumentClass.name,
                    ResultDocument.reason,
                    Document.summary,
                )
                .join(Document, Document.id == ResultDocument.document_id)
                .outerjoin(DocumentClass, DocumentClass.id == Document.document_class_id)
                .where(ResultDocument.result_id == parent_id)
                .order_by(Document.filename)
            )
        ).all()
    return "\n".join(
        f"- {fn} | {cls or '-'} | {(reason or '')[:120]} | {(summary or '')[:200]}"
        for fn, cls, reason, summary in rows
    )


async def _claim_count(answer_id: uuid.UUID) -> int:
    async with AsyncSessionLocal() as session:
        return int(
            (
                await session.execute(
                    select(AnswerClaim.id).where(AnswerClaim.answer_id == answer_id)
                )
            ).scalars().unique().all().__len__()
        )


async def _stage_synthesize(run_id: uuid.UUID, sinas: _Sinas) -> uuid.UUID:
    from app.models import Answer

    async with AsyncSessionLocal() as session:
        run = await session.get(QueryRun, run_id)
        question, parent_id = run.question, run.parent_result_id
        answer_id, chat_id = run.answer_id, run.synthesis_chat_id
        if answer_id and (run.telemetry or {}).get("draft", {}).get("completed"):
            return answer_id
        caller = _runner_caller(run)

    await _mark(run_id, status="synthesizing")
    if not answer_id:
        async with AsyncSessionLocal() as session:
            parent = await session.get(Result, parent_id)
            row = Answer(
                source_result_id=parent_id,
                question=question,
                owner_id=parent.owner_id,
                roles=list(parent.roles or []),
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            answer_id = row.id
        await _mark(run_id, answer_id=answer_id)
    _ = caller  # ownership derives from the parent result above

    if not chat_id:
        chat_id = await sinas.chat_create("grove/synthesis-agent", "[query-run] synthesis")
        manifest = await _doc_manifest(parent_id)
        sinas.send_detached(
            chat_id,
            f"Question: {question}\n\n"
            f"An answer has already been started for you: answer_id {answer_id} "
            f"(source result {parent_id}). Do NOT call start_answer.\n\n"
            "Your scope is DRAFTING ONLY: draft the claims with nested evidence per "
            "your workflow and playbook target, then reply exactly DRAFTING COMPLETE. "
            "Validation and publishing run outside your chat — do not call "
            "validate_answer_evidence or publish_answer.\n\n"
            "The result's documents (filename | class | provenance | summary):\n"
            + manifest,
        )
        await _mark(run_id, synthesis_chat_id=chat_id)
        await _tele(run_id, "draft", started=_iso(), chat_id=chat_id)

    nudges = 0
    deadline = asyncio.get_event_loop().time() + DRAFT_TIMEOUT_S
    last_n, stable_at = -1, asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(POLL_S)
        n = await _claim_count(answer_id)
        if n != last_n:
            last_n, stable_at = n, asyncio.get_event_loop().time()
            continue
        if not await _chat_is_idle(sinas, chat_id):
            continue
        settled = asyncio.get_event_loop().time() - stable_at > IDLE_DEAD_S
        if n >= MIN_CLAIMS and settled:
            await _tele(run_id, "draft", completed=_iso(), claims=n)
            return answer_id
        if nudges < MAX_NUDGES:
            nudges += 1
            sinas.send_detached(
                chat_id,
                f"Continue: answer {answer_id} has {n} claims. Draft the remaining "
                "claims with evidence per the playbook target, then reply DRAFTING COMPLETE.",
            )
        else:
            raise RuntimeError(f"synthesis drafting dead at {n} claims after {MAX_NUDGES} nudges")
    raise RuntimeError("synthesis drafting timed out")


async def _stage_validate_publish(run_id: uuid.UUID, sinas: _Sinas) -> None:
    from app.services.faithfulness import validate_answer_evidence

    async with AsyncSessionLocal() as session:
        run = await session.get(QueryRun, run_id)
        answer_id, chat_id = run.answer_id, run.synthesis_chat_id
        caller = _runner_caller(run)

    async def _await_chat_quiescence() -> None:
        """Never judge while the drafter is mid-write: require the synthesis
        chat to be continuously idle for IDLE_DEAD_S before proceeding (run 10
        failed on exactly this race — a validate round judged mid-remediation
        and the late resets had no round left)."""
        while not await _chat_is_idle(sinas, chat_id):
            await asyncio.sleep(POLL_S)

    await _mark(run_id, status="validating")
    for round_no in range(1, MAX_VALIDATE_ROUNDS + 1):
        await _await_chat_quiescence()
        async with AsyncSessionLocal() as session:
            verdict = await validate_answer_evidence(session, caller, answer_id, pending_only=True)
        await _tele(run_id, "validate", **{f"round_{round_no}": {
            "judged": verdict["judged"], "passed": verdict["passed"],
            "failed": len(verdict["failed"]), "errors": len(verdict["errors"]),
        }})
        if not verdict["failed"] and not verdict["errors"]:
            async with AsyncSessionLocal() as session:
                pending = (
                    await session.execute(
                        select(ClaimEvidence.id)
                        .join(AnswerClaim, AnswerClaim.id == ClaimEvidence.claim_id)
                        .where(AnswerClaim.answer_id == answer_id)
                        .where(ClaimEvidence.validated.is_(False))
                    )
                ).scalars().first()
                if pending is None:
                    from app.models import Answer

                    row = await session.get(Answer, answer_id)
                    row.status = "published"
                    row.published_at = _now()
                    await session.commit()
            await _tele(run_id, "validate", published=_iso())
            return
        if round_no == MAX_VALIDATE_ROUNDS:
            break
        failures = "\n".join(
            f"- claim seq {f['claim_sequence']} (claim_id {f['claim_id']}, evidence {f['evidence_id']}): {f['reason']}"
            for f in verdict["failed"]
        ) or "(transport errors only — rebind those spans)"
        sinas.send_detached(
            chat_id,
            "Validation results. Apply the TWO-STRIKES rule to these failed rows "
            "(update_claim to weaken, delete_claim if unsupportable, or bind ONE "
            "better span), then reply REMEDIATION COMPLETE. Do not validate or "
            "publish yourself:\n" + failures,
        )
        t0 = asyncio.get_event_loop().time()
        saw_activity = False
        while asyncio.get_event_loop().time() - t0 < REMEDIATION_WINDOW_S:
            await asyncio.sleep(POLL_S)
            idle = await _chat_is_idle(sinas, chat_id)
            if not idle:
                saw_activity = True
            elif saw_activity:
                break  # worked, then went quiet — remediation done
    raise RuntimeError("validation did not converge within round budget")


# ── entrypoint ──────────────────────────────────────────────────────────────


async def run_pipeline(run_id: uuid.UUID) -> None:
    """Drive one QueryRun to published/failed. Designed to be launched as an
    asyncio background task; safe to re-launch on a failed run (resume)."""
    sinas = _Sinas()
    await _mark(run_id, started_at=_now(), error=None)
    async with AsyncSessionLocal() as session:
        mode = (await session.get(QueryRun, run_id)).mode
    try:
        if mode in ("full", "retrieval"):
            await _stage_decompose(run_id, sinas)
            children = await _stage_search(run_id, sinas)
            await _stage_merge(run_id, children)
            await _stage_discovery(run_id, sinas)
        if mode == "retrieval":
            await _mark(run_id, status="published", completed_at=_now())
            _log.info("query run %s retrieval published", run_id)
            return
        # synthesis mode requires parent_result_id supplied at creation
        await _stage_synthesize(run_id, sinas)
        await _stage_validate_publish(run_id, sinas)
        await _mark(run_id, status="published", completed_at=_now())
        _log.info("query run %s published", run_id)
    except Exception as exc:
        _log.exception("query run %s failed", run_id)
        await _mark(run_id, status="failed", error=str(exc)[:2000], completed_at=_now())
