"""LLM intent parsing — maps a natural-language brief to routing primitives.

The decision framework routes on five request axes (job_type, source_shape,
evidence_risk, freshness_days, fields). Operators should not have to set any
of them: this module reads the brief with Claude (structured outputs) and
fills whatever the operator left unset. Operator-set values always win.

Without ANTHROPIC_API_KEY — or when the call fails — the router falls back to
the keyword heuristics in providers.py, so demo mode keeps working with no
key and no network.
"""

from __future__ import annotations

import logfire
from pydantic import BaseModel, Field

from ct_search.models import (
    EvidenceRisk,
    IntentOrigin,
    JobType,
    ResearchRequest,
    SourceShape,
)
from ct_search.settings import Settings

INTENT_MAX_TOKENS = 1024
INTENT_TIMEOUT_SECONDS = 20.0
MAX_SUGGESTED_FIELDS = 10

INTENT_SYSTEM = """\
You parse research briefs for Edna Search, a research and enrichment desk for
private-capital teams (placement agents, LP mapping, fund diligence, capital
formation). Map each brief to the routing primitives the desk's router
consumes. Be conservative: when a signal is absent, keep the default rather
than guessing.

job_type — what the user is doing:
- discover: find all entities matching criteria ("map every…", "find all…", "build a list of…")
- enrich: fill fields for known entities (a list is attached, or the brief names
  specific firms/people to complete)
- research: open-ended question answering on the live web (default)
- monitor: watch for new events over time ("alert me", "track", "watch")
- extract: pull structured content out of a known page or document
- brief: synthesize a narrative summary, memo, or report
- verify: corroborate or double-check specific claims

source_shape — where the answer lives:
- open_web: general live-web research (default)
- known_url: the brief contains specific URLs to read
- similar_to: find things like a named example ("funds similar to X")
- serp_vertical: Google verticals (Scholar, Patents, Maps)
- filings: SEC/EDGAR, Form ADV, Form D, and similar regulatory filings
- event_stream: ongoing monitoring of new events
- static_database: asks for data that lives in PitchBook/Preqin-style databases

evidence_risk — the cost of a wrong answer:
- low: desk scan, exploratory browsing
- medium: sourcing and outreach lists (default)
- high: diligence, IC memos, anything the user will cite to investors, an
  investment committee, or regulators

freshness_days — set only when the brief is time-sensitive: "this week" ≈ 7,
"this month" ≈ 30, "this quarter" ≈ 90, "this year" / "since <recent year>" ≈ 365,
"recent" / "latest" ≈ 90. Otherwise null.

suggested_fields — only for enrich or discover briefs: 4–8 snake_case output
columns the brief implies (e.g. firm, contact_name, role, email_status,
fund_size_usd, sector_focus, recent_signal, source_notes). Empty otherwise.

reasoning — one short sentence on the routing-relevant reading of the brief.
"""


class IntentSignals(BaseModel):
    """Routing primitives inferred from the brief — see docs/decision-framework.md."""

    job_type: JobType
    source_shape: SourceShape = "open_web"
    evidence_risk: EvidenceRisk = "medium"
    freshness_days: int | None = None
    suggested_fields: list[str] = Field(default_factory=list)
    reasoning: str = ""


async def infer_intent(request: ResearchRequest, settings: Settings) -> IntentSignals | None:
    """Ask Claude for the routing primitives. Returns None when unavailable."""
    if not settings.anthropic_api_key:
        return None
    try:
        import anthropic

        client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key,
            timeout=INTENT_TIMEOUT_SECONDS,
            max_retries=1,
        )
        response = await client.messages.parse(
            model=settings.ct_search_intent_model,
            max_tokens=INTENT_MAX_TOKENS,
            system=INTENT_SYSTEM,
            messages=[{"role": "user", "content": _intent_prompt(request)}],
            output_format=IntentSignals,
        )
        signals = response.parsed_output
        logfire.info(
            "intent_parsed {job_type} {source_shape}",
            job_type=signals.job_type,
            source_shape=signals.source_shape,
            evidence_risk=signals.evidence_risk,
            freshness_days=signals.freshness_days,
            suggested_fields=signals.suggested_fields,
        )
        return signals
    except Exception as exc:  # noqa: BLE001 — intent must never sink a run
        logfire.warn(
            "intent_parse_failed {error_type}",
            error_type=type(exc).__name__,
            error=str(exc)[:300],
        )
        return None


async def resolve_intent(
    request: ResearchRequest, settings: Settings
) -> tuple[ResearchRequest, IntentOrigin, str]:
    """Fill unset routing primitives on the request. Operator-set values win.

    Returns (resolved_request, intent_origin, intent_note) where intent_origin
    is "operator" when every primitive was already set, "llm" when Claude
    filled the gaps, and "heuristic" when the keyword fallback will.
    """
    open_slots = {
        "job_type": request.job_type is None,
        # The model default doubles as "unset" — the workbench never sends these
        # unless the operator tuned them.
        "source_shape": request.source_shape == "open_web",
        "evidence_risk": request.evidence_risk is None,
        "freshness_days": request.freshness_days is None,
        "fields": not request.fields,
    }

    if not any(open_slots.values()):
        return request.model_copy(), "operator", ""

    signals = await infer_intent(request, settings)
    updates: dict[str, object] = {}
    if signals is not None:
        if open_slots["job_type"]:
            updates["job_type"] = signals.job_type
        if open_slots["source_shape"] and signals.source_shape != "open_web":
            updates["source_shape"] = signals.source_shape
        if open_slots["evidence_risk"]:
            updates["evidence_risk"] = signals.evidence_risk
        if open_slots["freshness_days"] and signals.freshness_days:
            updates["freshness_days"] = min(signals.freshness_days, 3650)
        if open_slots["fields"] and request.mode == "enrich" and signals.suggested_fields:
            fields = [_normalize_field(field) for field in signals.suggested_fields]
            updates["fields"] = [field for field in fields if field][:MAX_SUGGESTED_FIELDS]

    # Whatever happens, the request leaving here carries a concrete risk level.
    if request.evidence_risk is None and "evidence_risk" not in updates:
        updates["evidence_risk"] = "medium"

    resolved = request.model_copy(update=updates)
    if signals is None:
        return resolved, "heuristic", ""
    return resolved, "llm", signals.reasoning.strip()


def _intent_prompt(request: ResearchRequest) -> str:
    lines = [f"Brief: {request.query.strip() or '(none — list attached without a brief)'}"]
    if request.rows:
        columns = sorted({str(key) for row in request.rows[:5] for key in row})
        lines.append(f"Attached list: {len(request.rows)} rows; columns: {', '.join(columns[:12])}")
    if request.fields:
        lines.append(f"Operator-selected fields: {', '.join(request.fields)}")
    lines.append(f"Mode: {request.mode}")
    return "\n".join(lines)


def _normalize_field(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in value.strip().lower())
    return "_".join(part for part in cleaned.split("_") if part)
