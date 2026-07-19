"""Stateless per-row faithfulness validation.

Each evidence verdict — does this span support this claim with this stance? —
is an independent judgment over ~1k tokens. Running it inside a conversational
validator agent made every verdict carry the whole answer transcript
(measured: $1.50–3 and minutes of sequential rounds per answer). Here Grove
assembles each (claim, span) pair server-side and fans them out as parallel
single-shot invocations of the tool-less `grove/evidence-check-agent`, then
writes the verdicts directly. No transcript, no enumeration round-trips, no
paging; usage still lands in Sinas's llm_usage ledger.

The judging model/prompt stays independent of the drafting agent, which is the
property the faithfulness gate actually needs — independence, not
conversation.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CallerIdentity
from app.config import get_settings
from app.models import AnswerClaim, ClaimEvidence, DocumentVersion

# Lines of context around the cited span; enough to judge support without
# re-litigating the document.
_SPAN_MARGIN = 5
_MAX_SPAN_CHARS = 4000
_MAX_CONCURRENCY = 8

_VALIDATOR_AGENT = "grove/evidence-check-agent"

_PROMPT = """CLAIM: {claim}

DECLARED STANCE: the cited span {stance_desc}.

CITED SPAN (lines {line_from}-{line_to} of the source document, with margin):
---
{span_text}
---

Does the span, as written, {stance_verb} the claim? Judge strictly: a span
that only tangentially relates, or asserts less than the claim does
(e.g. the claim states a precise figure the span does not contain), is a FAIL.
Reply with exactly one line:
PASS — <one short clause> | or | FAIL — <one short clause>"""

_STANCES = {
    "supports": ("is claimed to SUPPORT the claim", "support"),
    "contradicts": ("is claimed to CONTRADICT the claim", "contradict"),
    "qualifies": ("is claimed to QUALIFY (limit) the claim", "qualify"),
}


def _slice_span(content_md: str, span: dict[str, Any]) -> tuple[str, int, int]:
    lines = content_md.splitlines()
    total = len(lines)
    line_from = max(1, int(span.get("line_from") or 1) - _SPAN_MARGIN)
    line_to = min(total, int(span.get("line_to") or total) + _SPAN_MARGIN)
    text = "\n".join(lines[line_from - 1 : line_to])
    if len(text) > _MAX_SPAN_CHARS:
        text = text[:_MAX_SPAN_CHARS] + "\n[... span truncated for judging ...]"
    return text, line_from, line_to


async def _judge_one(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    settings,
    row: ClaimEvidence,
    claim_text: str,
    span_text: str,
    line_from: int,
    line_to: int,
) -> dict[str, Any]:
    stance_desc, stance_verb = _STANCES.get(row.stance, _STANCES["supports"])
    prompt = _PROMPT.format(
        claim=claim_text,
        stance_desc=stance_desc,
        stance_verb=stance_verb,
        line_from=line_from,
        line_to=line_to,
        span_text=span_text,
    )
    async with sem:
        try:
            resp = await client.post(
                f"{settings.sinas_url}/agents/{_VALIDATOR_AGENT}/invoke",
                headers={"Authorization": f"Bearer {settings.sinas_api_key}"},
                json={"message": prompt},
                timeout=90.0,
            )
            resp.raise_for_status()
            reply = (resp.json().get("reply") or "").strip()
        except Exception as exc:  # judged rows stay pending on transport errors
            return {"evidence_id": row.id, "error": f"invoke failed: {exc}"}

    first = reply.splitlines()[0].strip() if reply else ""
    upper = first.upper()
    if upper.startswith("PASS"):
        validated = True
    elif upper.startswith("FAIL"):
        validated = False
    else:
        return {"evidence_id": row.id, "error": f"unparseable verdict: {first[:80]}"}
    reasoning = first.split("—", 1)[1].strip() if "—" in first else first
    return {"evidence_id": row.id, "validated": validated, "reasoning": reasoning[:500]}


async def validate_answer_evidence(
    session: AsyncSession,
    caller: CallerIdentity,
    answer_id: uuid.UUID,
    pending_only: bool = True,
) -> dict[str, Any]:
    """Judge (pending) evidence rows of an answer as parallel stateless calls
    and record the verdicts. Returns a summary the synthesis agent can act on
    directly; rows whose judging errored stay unvalidated and are listed."""
    settings = get_settings()
    stmt = (
        select(ClaimEvidence, AnswerClaim)
        .join(AnswerClaim, AnswerClaim.id == ClaimEvidence.claim_id)
        .where(AnswerClaim.answer_id == answer_id)
        .order_by(AnswerClaim.sequence)
    )
    if pending_only:
        stmt = stmt.where(ClaimEvidence.validated.is_(False))
    rows = (await session.execute(stmt)).all()
    if not rows:
        return {"judged": 0, "passed": 0, "failed": [], "errors": []}

    # Resolve span text per row (documents may repeat across rows — cache).
    version_cache: dict[uuid.UUID, str | None] = {}

    async def _content_for(row: ClaimEvidence) -> str | None:
        vid = row.document_version_id
        if vid is None:
            dv = (
                await session.execute(
                    select(DocumentVersion)
                    .where(DocumentVersion.document_id == row.document_id)
                    .order_by(DocumentVersion.version.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if dv is None:
                return None
            vid = dv.id
            version_cache.setdefault(vid, dv.content_md)
        if vid not in version_cache:
            dv = await session.get(DocumentVersion, vid)
            version_cache[vid] = dv.content_md if dv else None
        return version_cache[vid]

    sem = asyncio.Semaphore(_MAX_CONCURRENCY)
    tasks = []
    errors: list[dict[str, Any]] = []
    async with httpx.AsyncClient() as client:
        for ev, claim in rows:
            content = await _content_for(ev)
            if not content:
                errors.append({"evidence_id": ev.id, "error": "no extracted content"})
                continue
            span_text, lf, lt = _slice_span(content, ev.span or {})
            tasks.append(
                _judge_one(client, sem, settings, ev, claim.claim_text, span_text, lf, lt)
            )
        verdicts = await asyncio.gather(*tasks) if tasks else []

    by_id = {ev.id: ev for ev, _ in rows}
    claims_by_ev = {ev.id: c for ev, c in rows}
    passed, failed = 0, []
    for v in verdicts:
        if "error" in v:
            errors.append(v)
            continue
        ev = by_id[v["evidence_id"]]
        ev.validated = v["validated"]
        ev.validation_reasoning = v["reasoning"]
        if v["validated"]:
            passed += 1
        else:
            failed.append(
                {
                    "evidence_id": str(ev.id),
                    "claim_id": str(ev.claim_id),
                    "claim_sequence": claims_by_ev[ev.id].sequence,
                    "reason": v["reasoning"],
                }
            )
    await session.commit()
    return {
        "judged": passed + len(failed),
        "passed": passed,
        "failed": failed,
        "errors": [
            {**e, "evidence_id": str(e["evidence_id"])} if not isinstance(e.get("evidence_id"), str) else e
            for e in errors
        ],
    }
