from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import logfire

from ct_search.intent import resolve_intent
from ct_search.models import (
    Evidence,
    FitResult,
    JobType,
    ProviderId,
    ProviderPublic,
    ResearchRequest,
    ResearchResponse,
    ResolvedEntity,
    ResultRow,
    RouteDecision,
    RouteStep,
    SourceShape,
    Thesis,
)
from ct_search.provider_knowledge import (
    DOWNSTREAM_TOKEN_PRICE_PER_1K_USD,
    KNOWLEDGE_REVIEWED_AT,
    provider_knowledge,
)
from ct_search.resolve import describe_basis, link, resolve_entity, resolve_local
from ct_search.settings import Settings
from ct_search.telemetry import (
    StepResult,
    log_route_plan,
    new_route_plan_id,
)
from ct_search.thesis import (
    judge_candidate,
    resolve_thesis,
    score_candidate,
    verdict_glyph,
)

# Decision-framework thresholds — see docs/decision-framework.md
WATERFALL_ROW_THRESHOLD = 50  # R6: enrich at scale forces waterfall
# Phase 4 — match vocabulary (intent fallback) and the per-criterion judge cost
# used only to surface a pre-run estimate caveat (the live value is a setting).
MATCH_TERMS: tuple[str, ...] = (
    "shortlist",
    "buyer list",
    "target list",
    "who should see this deal",
    "who should i show this deal",
    "which buyers",
    "which lps",
    "which investors",
    "best buyers for",
    "match this deal",
    "fit this deal",
    "find the counterpart",
    "natural buyers",
)
MATCH_DEFAULT_CRITERIA = 6
MATCH_TOP_N_VERIFY = 5
MATCH_MIN_CANDIDATES = 1
MATCH_SEED_LIMIT = 10
# Pre-run evidence estimate only — the live per-candidate cost uses the chosen
# provider's `estimated_search_cost` (the judge rate is always the setting).
DEFAULT_MATCH_EVIDENCE_COST = 0.007  # ~one routed search per candidate
HIGH_RISK_CITATION_FLOOR = 0.85  # R1: minimum citation capability for high evidence_risk
MEDIUM_RISK_CITATION_FLOOR = 0.70  # R1: minimum citation capability for medium evidence_risk
FRESHNESS_PENALTY_FLOOR = 0.2  # F1: how far the freshness multiplier can drop
SOURCE_SHAPE_UNSUPPORTED: dict[SourceShape, str] = {
    # R2 / architecture filter — vendors not wired today
    "serp_vertical": (
        "No SERP-class provider (SerpAPI/Google verticals) is wired today. "
        "Route runs against AI-native providers; Scholar/Patents/Maps coverage will be partial."
    ),
    "event_stream": (
        "No event-monitoring provider (Parallel Monitor) is wired today. "
        "Route falls back to on-demand search; signals will not arrive as push events."
    ),
    "static_database": (
        "Static enrichment databases (PitchBook, Preqin, Crunchbase) are out of Edna's scope. "
        "Route runs against live-web providers; baseline firmographics may be incomplete."
    ),
}


@dataclass(frozen=True)
class ProviderSpec:
    id: ProviderId
    label: str
    env_keys: tuple[str, ...]
    strengths: tuple[str, ...]
    estimated_search_cost: float
    estimated_row_cost: float
    speed_score: float
    quality_score: float
    coverage_score: float

    def available(self, settings: Settings) -> bool:
        return all(bool(getattr(settings, _env_to_attr(key), None)) for key in self.env_keys)


PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        id="parallel",
        label="Parallel",
        env_keys=("PARALLEL_API_KEY",),
        strengths=("cited web research", "structured enrichment", "agent-ready excerpts"),
        estimated_search_cost=0.005,
        estimated_row_cost=0.025,
        speed_score=0.78,
        quality_score=0.94,
        coverage_score=0.91,
    ),
    ProviderSpec(
        id="brave",
        label="Brave",
        env_keys=("BRAVE_API_KEY",),
        strengths=("low-latency web index", "fresh results", "cost control"),
        estimated_search_cost=0.005,
        estimated_row_cost=0.024,
        speed_score=0.92,
        quality_score=0.76,
        coverage_score=0.82,
    ),
    ProviderSpec(
        id="exa",
        label="Exa",
        env_keys=("EXA_API_KEY",),
        strengths=("semantic discovery", "company pages", "long-form snippets"),
        estimated_search_cost=0.007,
        estimated_row_cost=0.025,
        speed_score=0.72,
        quality_score=0.88,
        coverage_score=0.84,
    ),
    ProviderSpec(
        id="tavily",
        label="Tavily",
        env_keys=("TAVILY_API_KEY",),
        strengths=("general web retrieval", "balanced cost", "quick prototypes"),
        estimated_search_cost=0.008,
        estimated_row_cost=0.024,
        speed_score=0.86,
        quality_score=0.8,
        coverage_score=0.8,
    ),
    ProviderSpec(
        id="perplexity",
        label="Perplexity",
        env_keys=("PERPLEXITY_API_KEY",),
        strengths=("answer synthesis", "narrative summaries", "source-backed briefs"),
        estimated_search_cost=0.006,
        estimated_row_cost=0.032,
        speed_score=0.8,
        quality_score=0.86,
        coverage_score=0.78,
    ),
    ProviderSpec(
        id="edgar",
        label="EDGAR",
        env_keys=(),  # SEC full-text search is keyless — always live.
        strengths=("SEC filings full-text search", "primary-source citations", "no key required"),
        estimated_search_cost=0.0,
        estimated_row_cost=0.0,
        speed_score=0.82,
        quality_score=0.86,
        coverage_score=0.42,  # filings only — never a general-web contender
    ),
)

DEFAULT_FIELDS = [
    "firm",
    "role",
    "sector_focus",
    "geography",
    "email_status",
    "linkedin_profile",
    "recent_signal",
    "source_notes",
]


def public_providers(settings: Settings) -> list[ProviderPublic]:
    public: list[ProviderPublic] = []
    for spec in PROVIDERS:
        knowledge = provider_knowledge(spec.id)
        public.append(
            ProviderPublic(
                id=spec.id,
                label=spec.label,
                env_keys=list(spec.env_keys),
                strengths=list(spec.strengths),
                estimated_search_cost=spec.estimated_search_cost,
                estimated_row_cost=spec.estimated_row_cost,
                speed_score=spec.speed_score,
                quality_score=spec.quality_score,
                coverage_score=spec.coverage_score,
                available=spec.available(settings),
                best_for=list(knowledge.best_for),
                tradeoffs=list(knowledge.tradeoffs),
                avg_tokens_per_result=knowledge.economics.avg_tokens_per_result,
                avg_match_rate=knowledge.economics.avg_match_rate,
                metrics=list(knowledge.metrics),
            )
        )
    return public


def choose_provider(request: ResearchRequest, settings: Settings) -> RouteDecision:
    # Direct callers (eval harness, tests) may leave evidence_risk unset;
    # run_research resolves it via intent.py before getting here.
    if request.evidence_risk is None:
        request = request.model_copy(update={"evidence_risk": "medium"})
    specs = list(PROVIDERS)
    by_id = {spec.id: spec for spec in specs}
    rows = max(len(request.rows), 1)
    fields = max(len(request.fields or DEFAULT_FIELDS), 1)
    prompt_profile = _prompt_profile(request)
    job_type = _resolve_job_type(request)
    caveats = _framework_caveats(request, job_type, rows, settings)

    if request.routing_mode == "manual" and request.provider:
        selected = by_id[request.provider]
        considered = [_score_provider(spec, request, settings, rows, fields) for spec in specs]
        selected_score = _score_provider(selected, request, settings, rows, fields)
        steps = _route_steps(
            request=request,
            settings=settings,
            selected=selected,
            considered=considered,
            rows=rows,
            fields=fields,
            prompt_profile=prompt_profile,
            job_type=job_type,
        )
        processor_tier, processor_reason = _processor_for_request(request)
        return RouteDecision(
            provider=selected.id,
            label=selected.label,
            strategy="manual",
            routing_mode=request.routing_mode,
            reason=(
                f"Manual selection: {selected.label}. "
                f"Advisor fit: {selected_score['task_fit']:.0%}."
            ),
            score=selected_score["score"],
            estimated_cost=_estimate_cost(selected, request, rows, fields),
            available=selected.available(settings),
            considered=considered,
            steps=steps,
            prompt_profile=prompt_profile,
            knowledge_version=KNOWLEDGE_REVIEWED_AT,
            knowledge_sources=_knowledge_sources(steps),
            job_type=job_type,
            source_shape=request.source_shape,
            evidence_risk=request.evidence_risk,
            freshness_days=request.freshness_days,
            caveats=caveats,
            estimated_cost_per_grounded_row=_plan_cost_per_grounded_row(steps, by_id),
            processor_tier=processor_tier if selected.id == "parallel" else None,
            processor_reason=processor_reason if selected.id == "parallel" else "",
        )

    # Apply framework filters before ranking.
    eligible_specs = _apply_framework_filters(specs, request, job_type, caveats)
    candidates = [spec for spec in eligible_specs if spec.available(settings)] or eligible_specs
    scored = [_score_provider(spec, request, settings, rows, fields) for spec in candidates]
    selected_score = max(scored, key=lambda item: item["score"])
    selected = by_id[selected_score["id"]]
    considered = [_score_provider(spec, request, settings, rows, fields) for spec in specs]
    steps = _route_steps(
        request=request,
        settings=settings,
        selected=selected,
        considered=considered,
        rows=rows,
        fields=fields,
        prompt_profile=prompt_profile,
        job_type=job_type,
    )
    strategy = _route_strategy(request, prompt_profile, job_type, rows)
    reason = _route_reason(request.routing_mode, selected, selected_score["task_fit"], strategy)
    if not any(spec.available(settings) for spec in specs):
        reason += " No provider keys are configured, so the app will run in demo mode."

    processor_tier, processor_reason = _processor_for_request(request)
    return RouteDecision(
        provider=selected.id,
        label=selected.label,
        strategy=strategy,
        routing_mode=request.routing_mode,
        reason=reason,
        score=round(float(selected_score["score"]), 3),
        estimated_cost=_estimate_cost(selected, request, rows, fields),
        available=selected.available(settings),
        considered=considered,
        steps=steps,
        prompt_profile=prompt_profile,
        knowledge_version=KNOWLEDGE_REVIEWED_AT,
        knowledge_sources=_knowledge_sources(steps),
        job_type=job_type,
        source_shape=request.source_shape,
        evidence_risk=request.evidence_risk,
        freshness_days=request.freshness_days,
        caveats=caveats,
        estimated_cost_per_grounded_row=_plan_cost_per_grounded_row(steps, by_id),
        processor_tier=processor_tier if selected.id == "parallel" else None,
        processor_reason=processor_reason if selected.id == "parallel" else "",
    )


def _resolve_job_type(request: ResearchRequest) -> JobType:
    """Resolve the effective job_type, inferring from legacy mode when unset."""
    if request.job_type is not None:
        return request.job_type
    # A supplied thesis, or match vocabulary in the brief, means match — this
    # wins over enrich because a match run also carries a candidate list.
    text = " ".join([request.query or "", " ".join(request.fields or [])]).lower()
    if request.thesis is not None or _has_any(text, MATCH_TERMS):
        return "match"
    # Back-compat inference from the legacy `mode` field.
    if request.mode == "enrich":
        return "enrich"
    # mode == "search": pick the closest job_type from prompt signals.
    if _has_any(text, ("summarize", "brief", "memo", "report", "synthesize")):
        return "brief"
    if _has_any(text, ("monitor", "alert", "watch", "track")):
        return "monitor"
    if _has_any(text, ("find all", "list all", "every", "discover")):
        return "discover"
    return "research"


def _request_row_count(request: ResearchRequest, fallback_rows: int) -> int:
    """Best-available row count: explicit scale_hint wins, else len(request.rows)."""
    if request.scale_hint and request.scale_hint.rows is not None:
        return max(request.scale_hint.rows, 0)
    return len(request.rows) if request.rows else fallback_rows


def _match_cost_per_candidate(
    criteria: int, judge_per_criterion: float, search_cost: float
) -> float:
    """Per-candidate match cost: one evidence search + the judge over criteria.

    Single source of truth for both the pre-run caveat estimate and the live
    budget cap, so the quoted figure can never drift from what the cap enforces.
    """
    return judge_per_criterion * max(criteria, 1) + search_cost


def _framework_caveats(
    request: ResearchRequest, job_type: JobType, rows: int, settings: Settings
) -> list[str]:
    """Surface honest, operator-readable caveats per the decision framework."""
    caveats: list[str] = []
    # R2 / architecture filter — unsupported source shapes
    unsupported = SOURCE_SHAPE_UNSUPPORTED.get(request.source_shape)
    if unsupported:
        caveats.append(unsupported)
    # R5 — discover is best served by FindAll-class; flag when manual choice diverges
    if (
        job_type == "discover"
        and request.source_shape == "open_web"
        and request.routing_mode == "manual"
        and request.provider not in (None, "parallel")
    ):
        caveats.append(
            "Discovery jobs run best on entity-finding (Parallel FindAll). "
            "Manual choice may miss thesis-fit targets."
        )
    # R6 — waterfall at scale
    row_count = _request_row_count(request, rows)
    if job_type == "enrich" and row_count >= WATERFALL_ROW_THRESHOLD:
        caveats.append(
            f"Enrichment at {row_count} rows exceeds the single-provider match-rate ceiling "
            f"(~50–75% [vendor-reported, 2026]); waterfall fallbacks added to recover null fields."
        )
    # Phase 4 — match scoring cost scales with candidates × criteria; surface it
    # before the run so the budget cap is no surprise (docs/match-spec.md §2.4).
    if job_type == "match":
        criteria = (
            len(request.thesis.criteria)
            if request.thesis and request.thesis.criteria
            else MATCH_DEFAULT_CRITERIA
        )
        per_candidate = _match_cost_per_candidate(
            criteria,
            settings.ct_search_judge_cost_per_criterion_usd,
            DEFAULT_MATCH_EVIDENCE_COST,
        )
        caveats.append(
            f"Match scoring scales with candidates × criteria (~${per_candidate:.2f} "
            f"per candidate at {criteria} criteria); the run budget cap applies."
        )
    return caveats


def _eligible_for_shape(provider_id: ProviderId, source_shape: SourceShape) -> bool:
    """R2 — specialist indexes only compete inside their shape.

    EDGAR is always available (keyless), so without this gate the
    prefer-available promotion would route every open-web job to a
    filings-only index in key-less environments.
    """
    if provider_id == "edgar":
        return source_shape == "filings"
    return True


def _apply_framework_filters(
    specs: list[ProviderSpec],
    request: ResearchRequest,
    job_type: JobType,
    caveats: list[str],
) -> list[ProviderSpec]:
    """Apply R1 (evidence-risk floor), R2 (shape gate), R3 (similar_to) to candidates."""
    candidates = [
        spec for spec in specs if _eligible_for_shape(spec.id, request.source_shape)
    ]

    # R1: high evidence_risk requires providers with strong citation capability.
    if request.evidence_risk == "high":
        floor = HIGH_RISK_CITATION_FLOOR
    elif request.evidence_risk == "medium":
        floor = MEDIUM_RISK_CITATION_FLOOR
    else:
        floor = 0.0
    if floor > 0:
        eligible = [
            spec
            for spec in candidates
            if provider_knowledge(spec.id).capability_scores.get("citations", 0.0) >= floor
        ]
        if not eligible:
            # Fail loudly via caveat; do not silently degrade.
            caveats.append(
                f"No connected provider meets the citation floor ({floor:.0%}) "
                f"required by evidence_risk='{request.evidence_risk}'. "
                "Route runs with reduced audit confidence."
            )
        else:
            candidates = eligible

    return candidates


async def run_research(
    request: ResearchRequest,
    settings: Settings,
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
) -> ResearchResponse:
    """Run research by executing the route plan end-to-end.

    PR4 — this walks `route.steps` in order:
      primary  → run on full input
      fallback → re-run on rows the primary missed (null fields)
      verify   → re-run on low-confidence rows; mark verified on agreement
      synthesize → produce a bundled brief from grounded rows
    Each step's outcome is recorded as a `StepResult` in telemetry.

    `on_event(kind, payload)` receives progress as the plan executes
    (intent.resolved, route.planned, step.started/finished, budget.capped) so
    async runs can stream it to the workbench. Steps beyond the primary are
    skipped once their estimated cost would push the run past
    CT_SEARCH_MAX_RUN_BUDGET_USD.
    """

    def emit(kind: str, payload: dict[str, Any]) -> None:
        if on_event is None:
            return
        try:
            on_event(kind, payload)
        except Exception:  # noqa: BLE001 — a broken subscriber must not sink the run
            pass

    started = time.perf_counter()
    # Fill unset routing primitives from the brief (LLM when a key is present,
    # keyword heuristics otherwise). Operator-set values always win.
    request, intent_origin, intent_note = await resolve_intent(request, settings)
    route = choose_provider(request, settings)
    route.intent_origin = intent_origin
    route.intent_note = intent_note
    emit(
        "intent.resolved",
        {
            "origin": intent_origin,
            "note": intent_note,
            "job_type": route.job_type,
            "source_shape": route.source_shape,
            "evidence_risk": route.evidence_risk,
            "freshness_days": route.freshness_days,
        },
    )
    emit(
        "route.planned",
        {
            "strategy": route.strategy,
            "provider": route.provider,
            "label": route.label,
            "reason": route.reason,
            "estimated_cost": route.estimated_cost,
            "estimated_cost_per_grounded_row": route.estimated_cost_per_grounded_row,
            "steps": [
                {"role": step.role, "provider": step.provider, "label": step.label}
                for step in route.steps
            ],
        },
    )
    primary_spec = _spec(route.provider)
    route_plan_id = new_route_plan_id()
    warnings: list[str] = []

    rows: list[ResultRow] = []
    step_results: list[StepResult] = []
    is_demo = not route.available
    columns = (
        _enrichment_columns(request.rows, request.fields or DEFAULT_FIELDS)
        if request.mode == "enrich"
        else ["title", "url", "summary", "published_date"]
    )

    budget = settings.ct_search_max_run_budget_usd
    spent = 0.0
    thesis: Thesis | None = None

    # Phase 4 — match runs its own per-candidate pipeline instead of the
    # row-merge step loop (docs/match-spec.md §2). The route plan still carries
    # the primary (+ verifier) steps for display and telemetry.
    if route.job_type == "match":
        thesis = await resolve_thesis(request, settings)
        emit(
            "thesis.resolved",
            {
                "kind": thesis.kind,
                "origin": thesis.origin,
                "summary": thesis.summary,
                "criteria": [criterion.key for criterion in thesis.criteria],
            },
        )
        rows, columns, match_warnings, match_demo, step_results = await _run_match(
            request=request,
            settings=settings,
            route=route,
            thesis=thesis,
            primary_spec=primary_spec,
            budget=budget,
            emit=emit,
        )
        warnings.extend(match_warnings)
        is_demo = is_demo or match_demo

    match_steps = [] if route.job_type == "match" else route.steps
    for index, step in enumerate(match_steps):
        # The cap gates live spend only — demo steps are free, and skipping
        # them would gut the no-key walkthrough.
        step_live = _spec(step.provider).available(settings)
        if step.role != "primary" and step_live and spent + step.estimated_cost > budget:
            warnings.append(
                f"Run budget cap reached (${budget:.2f}, CT_SEARCH_MAX_RUN_BUDGET_USD): "
                f"skipped the {step.role} step on {step.label} and any remaining steps."
            )
            emit(
                "budget.capped",
                {
                    "budget_usd": budget,
                    "spent_usd": round(spent, 4),
                    "skipped_role": step.role,
                    "skipped_provider": step.provider,
                },
            )
            break

        step_spec = _spec(step.provider)
        step_started = time.perf_counter()
        step_error: str | None = None
        step_rows: list[ResultRow] = []
        emit(
            "step.started",
            {"index": index, "role": step.role, "provider": step.provider, "label": step.label},
        )

        try:
            if step.role == "primary":
                step_rows = await _execute_primary(step_spec, request, settings)
                _tag_rows(step_rows, role="primary")
                rows = step_rows
                demo_reason = (
                    _enrichment_demo_reason(settings, step_spec, len(request.rows))
                    if request.mode == "enrich"
                    else None
                )
                if not step_spec.available(settings) or demo_reason is not None:
                    is_demo = True
                    if demo_reason is not None and step_spec.available(settings):
                        warnings.append(demo_reason)

            elif step.role == "fallback":
                if request.mode == "search":
                    # For search: add unique URLs from the secondary provider.
                    step_rows = await _execute_primary(step_spec, request, settings)
                    _tag_rows(step_rows, role="fallback")
                    rows = _merge_search_rows(
                        rows, step_rows, max_results=request.max_results
                    )
                else:
                    missing = _rows_with_missing_fields(rows, request)
                    if missing:
                        target = [row.input for row in missing]
                        step_rows = await _run_enrichment(
                            step_spec, request, settings, target_rows=target
                        )
                        _tag_rows(step_rows, role="fallback")
                        rows = _merge_enrichment_rows(rows, step_rows)
                    # Else: nothing missed → skip but still log empty outcome.

            elif step.role == "verification":
                low_conf = [row for row in rows if row.confidence < 0.80]
                if not low_conf:
                    pass  # nothing to verify
                elif request.mode == "search":
                    step_rows = await _execute_primary(step_spec, request, settings)
                    _tag_rows(step_rows, role="verified")
                    rows = _apply_search_verification(rows, step_rows)
                else:
                    target = [row.input for row in low_conf]
                    step_rows = await _run_enrichment(
                        step_spec, request, settings, target_rows=target
                    )
                    _tag_rows(step_rows, role="verified")
                    rows = _apply_enrichment_verification(rows, step_rows)

            elif step.role == "synthesis":
                if rows:
                    synthesized = await _execute_synthesis(step_spec, request, settings, rows)
                    if synthesized:
                        _tag_rows(synthesized, role="synthesized")
                        rows = synthesized
                        step_rows = synthesized

        except Exception as exc:
            step_error = type(exc).__name__
            warnings.append(
                f"{step_spec.label} ({step.role}) returned an error: {exc}"
            )
            if step.role == "primary":
                # Fall back to demo so the operator still sees something.
                rows = (
                    _demo_enrichment(request, step_spec)
                    if request.mode == "enrich"
                    else _demo_search(request, step_spec)
                )
                _tag_rows(rows, role="primary")
                is_demo = True

        step_elapsed_ms = round((time.perf_counter() - step_started) * 1000)
        if step_live:
            spent += step.estimated_cost
        step_results.append(
            StepResult(
                provider=step.provider,
                role=step.role,
                latency_ms=step_elapsed_ms,
                cost_usd=step.estimated_cost,
                returned_rows=len(step_rows),
                null_rate=_null_rate(step_rows),
                citation_coverage=_citation_coverage(step_rows),
                avg_confidence=_avg_confidence(step_rows),
                low_confidence_rate=_low_confidence_rate(step_rows),
                error_type=step_error,
            )
        )
        emit(
            "step.finished",
            {
                "index": index,
                "role": step.role,
                "provider": step.provider,
                "label": step.label,
                "latency_ms": step_elapsed_ms,
                "returned_rows": len(step_rows),
                "error_type": step_error,
            },
        )

    if is_demo:
        warnings.append(
            "Demo mode: connect provider API keys to replace sample data with live research."
        )

    elapsed_ms = round((time.perf_counter() - started) * 1000)

    log_route_plan(
        route_plan_id=route_plan_id,
        request=request,
        decision=route,
        step_results=step_results,
    )

    return ResearchResponse(
        provider=primary_spec.id,
        provider_label=primary_spec.label,
        route=route,
        rows=rows,
        columns=columns,
        elapsed_ms=elapsed_ms,
        estimated_cost=route.estimated_cost,
        is_demo=is_demo,
        warnings=warnings,
        route_plan_id=route_plan_id,
        thesis=thesis,
    )


# --- Phase 4 — match pipeline ----------------------------------------------


async def _run_match(
    *,
    request: ResearchRequest,
    settings: Settings,
    route: RouteDecision,
    thesis: Thesis,
    primary_spec: ProviderSpec,
    budget: float,
    emit: Callable[[str, dict[str, Any]], None],
) -> tuple[list[ResultRow], list[str], list[str], bool, list[StepResult]]:
    """Resolve → evidence → judge → score → (verify) → rank, per candidate.

    Returns (rows, columns, warnings, is_demo, step_results).
    """
    warnings: list[str] = []
    providers_live = primary_spec.available(settings)
    judge_live = bool(settings.anthropic_api_key)
    is_demo = not providers_live

    candidates = list(request.rows)
    if not candidates:
        candidates = await _seed_match_candidates(primary_spec, request, thesis, settings)
        if candidates:
            warnings.append(
                f"No candidate list supplied; seeded {len(candidates)} candidates from a "
                "discovery search. Upload a list for a complete shortlist."
            )
    if not candidates:
        warnings.append("Match needs candidates: upload a list or broaden the brief.")
        return [], _match_columns(thesis, []), warnings, is_demo, []

    criteria_count = max(len(thesis.criteria), 1)
    per_candidate_cost = _match_cost_per_candidate(
        criteria_count,
        settings.ct_search_judge_cost_per_criterion_usd,
        primary_spec.estimated_search_cost,
    )
    if providers_live and per_candidate_cost > 0:
        affordable = max(int(budget / per_candidate_cost), MATCH_MIN_CANDIDATES)
    else:
        affordable = len(candidates)
    scored = candidates[:affordable]
    if len(candidates) > len(scored):
        warnings.append(
            f"Run budget cap (${budget:.2f}) scored the first {len(scored)} of "
            f"{len(candidates)} candidates at ~${per_candidate_cost:.3f} each."
        )
        emit(
            "budget.capped",
            {"budget_usd": budget, "scored": len(scored), "total": len(candidates)},
        )
    if providers_live and not judge_live:
        warnings.append(
            "Evidence gathered but not scored: set ANTHROPIC_API_KEY to judge thesis fit. "
            "Every criterion shows as unknown until then."
        )

    emit("match.started", {"candidates": len(scored), "criteria": criteria_count})

    rows: list[ResultRow] = []
    edgar_used = False
    for index, candidate in enumerate(scored):
        entity = await asyncio.to_thread(resolve_entity, candidate, settings)
        label = _entity_label(candidate, index + 1)
        citations, fields, used_edgar = await _gather_match_evidence(
            primary_spec, request, settings, thesis, entity, label
        )
        edgar_used = edgar_used or used_edgar
        verdicts = await judge_candidate(
            thesis, label, fields, citations, settings, demo=is_demo
        )
        fit = score_candidate(thesis, verdicts)
        rows.append(_match_row(candidate, entity, fit, citations, primary_spec.id))
        emit(
            "match.scored",
            {"index": index, "label": label, "fit": fit.fit, "band": fit.band},
        )

    verify_count = 0
    if route.evidence_risk == "high" and providers_live and judge_live:
        rows, verify_count = await _verify_match_rows(
            rows, request, settings, thesis, primary_spec
        )

    rows = _rank_match_rows(rows)
    columns = _match_columns(thesis, rows)

    step_results = [
        StepResult(
            provider=primary_spec.id,
            role="primary",
            returned_rows=len(rows),
            citation_coverage=_citation_coverage(rows),
            avg_confidence=_avg_confidence(rows),
            low_confidence_rate=_low_confidence_rate(rows),
        )
    ]
    if edgar_used:
        step_results.append(
            StepResult(provider="edgar", role="fallback", returned_rows=len(rows))
        )
    if verify_count:
        step_results.append(
            StepResult(
                provider=primary_spec.id, role="verification", returned_rows=verify_count
            )
        )
    return rows, columns, warnings, is_demo, step_results


async def _seed_match_candidates(
    primary_spec: ProviderSpec,
    request: ResearchRequest,
    thesis: Thesis,
    settings: Settings,
) -> list[dict[str, Any]]:
    """Discover candidates from the thesis when no list was uploaded (spec §1.1 step 2)."""
    seed_request = request.model_copy(
        update={
            "query": thesis.summary or request.query,
            "mode": "search",
            "max_results": MATCH_SEED_LIMIT,
        }
    )
    try:
        results = await _run_search(primary_spec, seed_request, settings)
    except Exception:
        return []
    candidates: list[dict[str, Any]] = []
    for row in results[:MATCH_SEED_LIMIT]:
        title = str(row.fields.get("title") or "").strip()
        if not title:
            continue
        candidates.append({"firm": title, "website": str(row.fields.get("url") or "")})
    return candidates


async def _gather_match_evidence(
    primary_spec: ProviderSpec,
    request: ResearchRequest,
    settings: Settings,
    thesis: Thesis,
    entity: ResolvedEntity,
    label: str,
) -> tuple[list[Evidence], dict[str, Any], bool]:
    """Gather per-candidate evidence: routed web search, plus EDGAR when a CIK is known."""
    evidence_request = request.model_copy(
        update={
            "query": _match_evidence_query(thesis, label),
            "mode": "search",
            "max_results": 5,
        }
    )
    try:
        search_rows = await _run_search(primary_spec, evidence_request, settings)
    except Exception:
        search_rows = []
    snippet, citations = _summarize_search_rows(search_rows)
    fields: dict[str, Any] = {"source_notes": snippet} if snippet else {}

    used_edgar = False
    if entity.cik:
        edgar_request = request.model_copy(
            update={
                "query": entity.name or label,
                "mode": "search",
                "source_shape": "filings",
                "max_results": 3,
            }
        )
        try:
            edgar_rows = await _edgar_search(edgar_request, settings)
        except Exception:
            edgar_rows = []
        if edgar_rows:
            used_edgar = True
            _filings_snippet, edgar_citations = _summarize_search_rows(edgar_rows)
            citations = citations + edgar_citations
            fields["filings"] = "; ".join(
                str(row.fields.get("title", "")) for row in edgar_rows[:3]
            )
    return citations[:8], fields, used_edgar


def _match_evidence_query(thesis: Thesis, label: str) -> str:
    criteria_hint = "; ".join(criterion.description for criterion in thesis.criteria[:4])
    summary = thesis.summary or "this deal"
    return _compact(f"{label}: evidence on fit for {summary}. Assess: {criteria_hint}", 300)


def _match_row(
    candidate: dict[str, Any],
    entity: ResolvedEntity,
    fit: FitResult,
    fallback_citations: list[Evidence],
    provider_id: str,
) -> ResultRow:
    basis = describe_basis(entity)
    fields: dict[str, Any] = {
        "fit": fit.band,
        "fit_score": f"{fit.fit:.2f}",
    }
    for verdict in fit.verdicts:
        fields[verdict.key] = verdict_glyph(verdict)
    fields["match_basis"] = basis
    fields["disqualifiers"] = "; ".join(fit.disqualifiers)

    union: list[Evidence] = []
    seen: set[str] = set()
    for verdict in fit.verdicts:
        for citation in verdict.citations:
            if citation.url and citation.url not in seen:
                union.append(citation)
                seen.add(citation.url)
    if not union:
        union = fallback_citations[:5]

    return ResultRow(
        input=candidate,
        fields=fields,
        confidence=fit.fit,
        citations=union[:8],
        provider=provider_id,
        step_role="match",
        match_basis=basis,
        fit_result=fit,
    )


def _rank_match_rows(rows: list[ResultRow]) -> list[ResultRow]:
    """Fit-ranked, with disqualified candidates sunk to the bottom (still visible)."""

    def sort_key(row: ResultRow) -> tuple[int, float]:
        fit = row.fit_result
        disqualified = 1 if (fit and fit.band == "disqualified") else 0
        return (disqualified, -(fit.fit if fit else 0.0))

    return sorted(rows, key=sort_key)


def _match_columns(thesis: Thesis, rows: list[ResultRow]) -> list[str]:
    input_columns: list[str] = []
    for row in rows:
        for key in row.input:
            key = str(key)
            if key not in input_columns:
                input_columns.append(key)
    criterion_columns = [criterion.key for criterion in thesis.criteria]
    return [
        *input_columns,
        "fit",
        "fit_score",
        *criterion_columns,
        "match_basis",
        "disqualifiers",
    ]


async def _verify_match_rows(
    rows: list[ResultRow],
    request: ResearchRequest,
    settings: Settings,
    thesis: Thesis,
    primary_spec: ProviderSpec,
) -> tuple[list[ResultRow], int]:
    """Re-gather and re-judge disqualifiers + top-N (the verifier asymmetry, §1.3).

    A disqualifier that does not reproduce on independent evidence is dropped;
    surviving rows are flagged `verified` for the ledger.
    """
    ranked = _rank_match_rows(rows)
    targets: set[int] = set()
    for position, row in enumerate(ranked):
        fit = row.fit_result
        if position < MATCH_TOP_N_VERIFY or (fit and fit.disqualifiers):
            targets.add(id(row))

    updated: list[ResultRow] = []
    verified = 0
    for row in rows:
        if id(row) not in targets:
            updated.append(row)
            continue
        entity = await asyncio.to_thread(resolve_entity, row.input, settings)
        label = _entity_label(row.input, 1)
        citations, fields, _used_edgar = await _gather_match_evidence(
            primary_spec, request, settings, thesis, entity, label
        )
        verdicts = await judge_candidate(thesis, label, fields, citations, settings)
        fit = score_candidate(thesis, verdicts)
        new_row = _match_row(row.input, entity, fit, citations, primary_spec.id)
        new_row.verified = True
        updated.append(new_row)
        verified += 1
    return updated, verified


# --- PR4 plan-execution helpers --------------------------------------------


async def _execute_primary(
    spec: ProviderSpec,
    request: ResearchRequest,
    settings: Settings,
) -> list[ResultRow]:
    if request.mode == "enrich":
        return await _run_enrichment(spec, request, settings)
    return await _run_search(spec, request, settings)


def _tag_rows(rows: list[ResultRow], *, role: str) -> None:
    for row in rows:
        row.step_role = role
        if row.provider and row.provider not in row.contributing_providers:
            row.contributing_providers.append(row.provider)


def _rows_with_missing_fields(
    rows: list[ResultRow], request: ResearchRequest
) -> list[ResultRow]:
    """Rows where at least one requested field is blank."""
    requested = request.fields or DEFAULT_FIELDS
    out: list[ResultRow] = []
    for row in rows:
        for field in requested:
            value = row.fields.get(field)
            if value in (None, "", []):
                out.append(row)
                break
    return out


def _merge_enrichment_rows(
    primary: list[ResultRow], supplement: list[ResultRow]
) -> list[ResultRow]:
    """Overlay supplement field values onto primary rows wherever primary is blank.

    Phase 4 — rows are matched by resolved-entity `link()` verdict (certain or
    probable), not exact-dict equality, so "KKR & Co." and "KKR & Co. Inc."
    merge instead of double-counting. Falls back to exact-key matching when a
    row carries no resolvable identity. Preserves primary's provider
    attribution for its non-blank fields; appends the supplement provider to
    `contributing_providers` when any field was filled.
    """
    supplement_for = _link_supplement_index(primary, supplement)
    merged: list[ResultRow] = []
    for position, row in enumerate(primary):
        supp = supplement_for.get(position)
        if supp is None:
            merged.append(row)
            continue
        filled_any = False
        new_fields = dict(row.fields)
        for field, value in supp.fields.items():
            if value not in (None, "", []) and new_fields.get(field) in (None, "", []):
                new_fields[field] = value
                filled_any = True
        new_citations = list(row.citations)
        if filled_any:
            new_citations.extend(supp.citations[:2])
        contributors = list(row.contributing_providers)
        if filled_any and supp.provider and supp.provider not in contributors:
            contributors.append(supp.provider)
        merged.append(
            row.model_copy(
                update={
                    "fields": new_fields,
                    "citations": new_citations,
                    "contributing_providers": contributors,
                }
            )
        )
    return merged


def _merge_search_rows(
    primary: list[ResultRow], supplement: list[ResultRow], *, max_results: int
) -> list[ResultRow]:
    """For search: add unique URLs from supplement to primary, up to max_results."""
    seen_urls = {str(row.fields.get("url", "")) for row in primary if row.fields.get("url")}
    merged = list(primary)
    for row in supplement:
        url = str(row.fields.get("url", ""))
        if url and url not in seen_urls and len(merged) < max_results:
            merged.append(row)
            seen_urls.add(url)
    return merged


def _apply_enrichment_verification(
    rows: list[ResultRow], verified_rows: list[ResultRow]
) -> list[ResultRow]:
    """Mark rows verified when an independent provider agrees on key fields.

    Phase 4 — pairs rows to their verifier by resolved-entity `link()`, with
    exact-key fallback (see `_merge_enrichment_rows`).
    """
    verifier_for = _link_supplement_index(rows, verified_rows)
    updated: list[ResultRow] = []
    for position, row in enumerate(rows):
        verifier = verifier_for.get(position)
        if verifier is None:
            updated.append(row)
            continue
        agreement = _field_agreement(row.fields, verifier.fields)
        contributors = list(row.contributing_providers)
        if verifier.provider and verifier.provider not in contributors:
            contributors.append(verifier.provider)
        if agreement >= 0.5:
            updated.append(
                row.model_copy(
                    update={
                        "verified": True,
                        "confidence": min(1.0, round(row.confidence + 0.1, 2)),
                        "citations": row.citations + verifier.citations[:2],
                        "contributing_providers": contributors,
                    }
                )
            )
        else:
            # Disagreement: keep original, surface a dispute note.
            note_citation = Evidence(
                title=f"Verifier disagreement ({verifier.provider})",
                url=verifier.citations[0].url if verifier.citations else "",
                excerpt=_compact(str(verifier.fields), 220),
            )
            updated.append(
                row.model_copy(
                    update={
                        "citations": row.citations + [note_citation],
                        "contributing_providers": contributors,
                    }
                )
            )
    return updated


def _apply_search_verification(
    rows: list[ResultRow], verified_rows: list[ResultRow]
) -> list[ResultRow]:
    """Mark search rows verified when their URL also appears in the verifier's results."""
    verifier_urls = {
        str(row.fields.get("url", "")) for row in verified_rows if row.fields.get("url")
    }
    updated: list[ResultRow] = []
    for row in rows:
        url = str(row.fields.get("url", ""))
        if url and url in verifier_urls:
            contributors = list(row.contributing_providers)
            for verifier_row in verified_rows:
                if (
                    str(verifier_row.fields.get("url", "")) == url
                    and verifier_row.provider not in contributors
                ):
                    contributors.append(verifier_row.provider)
            updated.append(
                row.model_copy(
                    update={
                        "verified": True,
                        "confidence": min(1.0, round(row.confidence + 0.08, 2)),
                        "contributing_providers": contributors,
                    }
                )
            )
        else:
            updated.append(row)
    return updated


def _link_supplement_index(
    primary: list[ResultRow], supplement: list[ResultRow]
) -> dict[int, ResultRow]:
    """Map each primary row position to a supplement row via identity linkage.

    Prefers a `link()` verdict at certain/probable; falls back to exact input
    equality when either side has no resolvable identity. Each supplement row
    is consumed at most once.
    """
    supplement_entities = [(resolve_local(row.input), row) for row in supplement]
    used: set[int] = set()
    mapping: dict[int, ResultRow] = {}
    for position, primary_row in enumerate(primary):
        primary_entity = resolve_local(primary_row.input)
        matched: int | None = None
        if primary_entity.basis != "none":
            for supplement_position, (entity, _row) in enumerate(supplement_entities):
                if supplement_position in used or entity.basis == "none":
                    continue
                if link(primary_entity, entity).linked:
                    matched = supplement_position
                    break
        if matched is None:
            primary_key = _input_key(primary_row.input)
            for supplement_position, (_entity, row) in enumerate(supplement_entities):
                if supplement_position in used:
                    continue
                if _input_key(row.input) == primary_key:
                    matched = supplement_position
                    break
        if matched is not None:
            used.add(matched)
            mapping[position] = supplement_entities[matched][1]
    return mapping


def _field_agreement(left: dict[str, Any], right: dict[str, Any]) -> float:
    keys = [
        key for key in left
        if left.get(key) not in (None, "", []) and right.get(key) not in (None, "", [])
    ]
    if not keys:
        return 0.0
    matches = 0
    for key in keys:
        left_val = str(left.get(key) or "").lower().strip()
        right_val = str(right.get(key) or "").lower().strip()
        if not (left_val and right_val):
            continue
        if (
            left_val in right_val
            or right_val in left_val
            or _shared_token_ratio(left_val, right_val) >= 0.4
        ):
            matches += 1
    return matches / len(keys)


def _shared_token_ratio(left: str, right: str) -> float:
    left_tokens = {token for token in re.split(r"\W+", left) if len(token) > 2}
    right_tokens = {token for token in re.split(r"\W+", right) if len(token) > 2}
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / max(len(left_tokens | right_tokens), 1)


def _input_key(input_row: dict[str, Any]) -> str:
    """Stable identity for a row based on its input dict."""
    serialized = json.dumps(input_row, sort_keys=True, default=str)
    return hashlib.sha1(serialized.encode("utf-8")).hexdigest()


async def _execute_synthesis(
    spec: ProviderSpec,
    request: ResearchRequest,
    settings: Settings,
    grounded_rows: list[ResultRow],
) -> list[ResultRow]:
    """Brief synthesis: re-route the grounded results through a narrative provider.

    For now we delegate to the provider's search API with a synthesis-shaped
    query. This produces a single ResultRow that summarizes the retrieval leg.
    """
    if not grounded_rows:
        return []
    excerpt_blob = "\n\n".join(
        f"- {row.fields.get('title', '')}: {row.fields.get('summary', '')}"
        for row in grounded_rows[:6]
    )
    synthesis_query = (
        f"Synthesize a one-paragraph brief on: {request.query}. "
        f"Use these excerpts as the evidence base:\n{excerpt_blob[:1800]}"
    )
    synth_request = request.model_copy(
        update={"query": synthesis_query, "mode": "search", "max_results": 1}
    )
    try:
        synth_rows = await _run_search(spec, synth_request, settings)
    except Exception:
        return []
    # Use the synthesized row as a single result, attaching the union of citations.
    if not synth_rows:
        return []
    union_citations: list[Evidence] = []
    seen_urls: set[str] = set()
    for row in grounded_rows:
        for cite in row.citations:
            if cite.url and cite.url not in seen_urls:
                union_citations.append(cite)
                seen_urls.add(cite.url)
    head = synth_rows[0]
    return [
        head.model_copy(
            update={
                "citations": union_citations[:8],
                "provider": spec.id,
            }
        )
    ]


def _null_rate(rows: list[ResultRow]) -> float:
    if not rows:
        return 1.0
    total = sum(max(len(row.fields), 1) for row in rows)
    blanks = sum(1 for row in rows for value in row.fields.values() if value in (None, "", []))
    return round(blanks / total, 3) if total else 1.0


def _citation_coverage(rows: list[ResultRow]) -> float:
    if not rows:
        return 0.0
    cited = sum(1 for row in rows if row.citations)
    return round(cited / len(rows), 3)


def _avg_confidence(rows: list[ResultRow]) -> float:
    if not rows:
        return 0.0
    return round(sum(row.confidence for row in rows) / len(rows), 3)


def _low_confidence_rate(rows: list[ResultRow]) -> float:
    if not rows:
        return 0.0
    low = sum(1 for row in rows if row.confidence < 0.7)
    return round(low / len(rows), 3)


async def _run_search(
    spec: ProviderSpec,
    request: ResearchRequest,
    settings: Settings,
) -> list[ResultRow]:
    if not spec.available(settings):
        return _demo_search(request, spec)
    # Extraction route: when the brief names URLs and the shape says so,
    # extract-capable venues pull the pages instead of searching for them.
    if request.source_shape == "known_url":
        urls = _extract_urls(request.query)
        if urls:
            if spec.id == "tavily":
                return await _tavily_extract(urls, request, settings)
            if spec.id == "exa":
                return await _exa_contents(urls, request, settings)
    if spec.id == "parallel":
        return await _parallel_search(request, settings)
    if spec.id == "brave":
        return await _brave_search(request, settings)
    if spec.id == "exa":
        return await _exa_search(request, settings)
    if spec.id == "tavily":
        return await _tavily_search(request, settings)
    if spec.id == "perplexity":
        return await _perplexity_search(request, settings)
    if spec.id == "edgar":
        return await _edgar_search(request, settings)
    return _demo_search(request, spec)


async def _run_enrichment(
    spec: ProviderSpec,
    request: ResearchRequest,
    settings: Settings,
    target_rows: list[dict[str, Any]] | None = None,
) -> list[ResultRow]:
    """Enrich rows with a provider.

    `target_rows` overrides `request.rows` so the executor can re-run a
    fallback or verifier step against just the missed/low-confidence subset.
    """
    rows_to_use = target_rows if target_rows is not None else request.rows
    if not rows_to_use:
        return []

    if spec.id == "parallel" and _enrichment_demo_reason(settings, spec, len(rows_to_use)) is None:
        return await _parallel_task_enrichment(request, settings, target_rows=rows_to_use)
    if spec.available(settings) and spec.id != "parallel":
        # PR4 — non-Parallel enrichment uses targeted per-row search +
        # snippet capture. It can't fill structured fields the way Task can,
        # but it produces real, independent, cited rows that the merge step
        # can use to fill nulls / corroborate primary rows.
        return await _targeted_search_enrichment(spec, request, settings, rows_to_use)
    return _demo_enrichment_for(request, spec, rows_to_use)


async def _parallel_search(request: ResearchRequest, settings: Settings) -> list[ResultRow]:
    try:
        return await asyncio.to_thread(_parallel_sdk_search, request, settings)
    except Exception:
        return await _parallel_rest_search(request, settings)


def _parallel_sdk_search(request: ResearchRequest, settings: Settings) -> list[ResultRow]:
    from parallel import Parallel

    client = Parallel(api_key=settings.parallel_api_key)
    response = client.search(
        objective=request.query,
        search_queries=_derive_search_queries(request.query),
        max_chars_total=9000,
    )
    return _parallel_results_to_rows(_to_mapping(response), request)


async def _parallel_rest_search(request: ResearchRequest, settings: Settings) -> list[ResultRow]:
    payload = {
        "objective": request.query,
        "search_queries": _derive_search_queries(request.query),
        "max_chars_total": 9000,
    }
    async with httpx.AsyncClient(timeout=40) as client:
        response = await client.post(
            "https://api.parallel.ai/v1/search",
            headers={
                "Content-Type": "application/json",
                "x-api-key": settings.parallel_api_key or "",
            },
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
    return _parallel_results_to_rows(data, request)


async def _brave_search(request: ResearchRequest, settings: Settings) -> list[ResultRow]:
    params = {"q": request.query, "count": str(request.max_results)}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params=params,
            headers={
                "X-Subscription-Token": settings.brave_api_key or "",
                "Accept": "application/json",
            },
        )
        response.raise_for_status()
        data = response.json()
    results = data.get("web", {}).get("results", [])
    return [
        _search_result_row(
            provider="brave",
            query=request.query,
            title=item.get("title", "Untitled result"),
            url=item.get("url", ""),
            summary=" ".join(
                str(part)
                for part in [item.get("description"), *(item.get("extra_snippets") or [])]
                if part
            ),
            published_date=item.get("age", ""),
        )
        for item in results[: request.max_results]
    ]


async def _exa_search(request: ResearchRequest, settings: Settings) -> list[ResultRow]:
    payload = {
        "query": request.query,
        "numResults": request.max_results,
        "type": "auto",
        "contents": {"text": {"maxCharacters": 3000}, "highlights": True},
    }
    async with httpx.AsyncClient(timeout=40) as client:
        response = await client.post(
            "https://api.exa.ai/search",
            headers={"Content-Type": "application/json", "x-api-key": settings.exa_api_key or ""},
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
    return [
        _search_result_row(
            provider="exa",
            query=request.query,
            title=item.get("title", "Untitled result"),
            url=item.get("url", ""),
            summary="\n\n".join(
                str(part)
                for part in [item.get("text"), *(item.get("highlights") or [])]
                if part
            ),
            published_date=item.get("publishedDate", ""),
        )
        for item in data.get("results", [])[: request.max_results]
    ]


async def _tavily_search(request: ResearchRequest, settings: Settings) -> list[ResultRow]:
    payload = {"query": request.query, "max_results": request.max_results, "search_depth": "basic"}
    async with httpx.AsyncClient(timeout=35) as client:
        response = await client.post(
            "https://api.tavily.com/search",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {settings.tavily_api_key or ''}",
            },
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
    return [
        _search_result_row(
            provider="tavily",
            query=request.query,
            title=item.get("title", "Untitled result"),
            url=item.get("url", ""),
            summary=item.get("content", ""),
            published_date=item.get("published_date", ""),
        )
        for item in data.get("results", [])[: request.max_results]
    ]


async def _perplexity_search(request: ResearchRequest, settings: Settings) -> list[ResultRow]:
    # Deep-research profiles escalate to sonar-pro (multi-hop grounding);
    # plain lookups stay on the cheaper sonar.
    model = "sonar-pro" if _prompt_profile(request)["needs_deep_research"] else "sonar"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": request.query}],
    }
    async with httpx.AsyncClient(timeout=50) as client:
        response = await client.post(
            "https://api.perplexity.ai/chat/completions",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {settings.perplexity_api_key or ''}",
            },
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    citations = [
        Evidence(title=f"Source {index + 1}", url=url, excerpt="")
        for index, url in enumerate(data.get("citations", []))
    ]
    return [
        ResultRow(
            input={"query": request.query},
            fields={
                "title": "Perplexity answer",
                "url": citations[0].url if citations else "",
                "summary": content,
                "published_date": "",
            },
            confidence=0.82,
            citations=citations,
            provider="perplexity",
        )
    ]


# --- Extraction (known_url) --------------------------------------------------

_URL_PATTERN = re.compile(r"https?://[^\s)\]}>\"']+")


def _extract_urls(text: str) -> list[str]:
    """URLs named in the brief, deduped, capped to keep extract calls bounded."""
    urls = [url.rstrip(".,;") for url in _URL_PATTERN.findall(text or "")]
    return list(dict.fromkeys(urls))[:10]


async def _tavily_extract(
    urls: list[str], request: ResearchRequest, settings: Settings
) -> list[ResultRow]:
    """Tavily Extract — pull page content for URLs named in the brief."""
    async with httpx.AsyncClient(timeout=45) as client:
        response = await client.post(
            "https://api.tavily.com/extract",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {settings.tavily_api_key or ''}",
            },
            json={"urls": urls},
        )
        response.raise_for_status()
        data = response.json()
    return _extract_results_to_rows(
        [
            {"url": item.get("url", ""), "text": item.get("raw_content", "")}
            for item in data.get("results", [])
        ],
        provider="tavily",
    )


async def _exa_contents(
    urls: list[str], request: ResearchRequest, settings: Settings
) -> list[ResultRow]:
    """Exa /contents — extraction fallback when Tavily is not connected."""
    async with httpx.AsyncClient(timeout=45) as client:
        response = await client.post(
            "https://api.exa.ai/contents",
            headers={"Content-Type": "application/json", "x-api-key": settings.exa_api_key or ""},
            json={"urls": urls, "text": True},
        )
        response.raise_for_status()
        data = response.json()
    return _extract_results_to_rows(
        [
            {
                "url": item.get("url", ""),
                "title": item.get("title", ""),
                "text": item.get("text", ""),
            }
            for item in data.get("results", [])
        ],
        provider="exa",
    )


def _extract_results_to_rows(items: list[dict[str, str]], provider: str) -> list[ResultRow]:
    rows: list[ResultRow] = []
    for item in items:
        url = item.get("url", "")
        if not url:
            continue
        text = (item.get("text") or "").strip()
        title = item.get("title") or url.split("//")[-1].split("?")[0]
        rows.append(
            ResultRow(
                fields={
                    "title": title,
                    "url": url,
                    "summary": _compact(text, 600) if text else "No extractable content.",
                    "published_date": "",
                },
                confidence=0.88 if text else 0.3,
                citations=[Evidence(title=title, url=url, excerpt=_compact(text, 200))],
                provider=provider,
            )
        )
    return rows


EDGAR_FTS_URL = "https://efts.sec.gov/LATEST/search-index"
# Brief keywords → EDGAR `forms` filter. Form ADV is deliberately absent — it
# lives on adviserinfo.sec.gov, outside EDGAR full-text search.
_EDGAR_FORM_HINTS: tuple[tuple[str, str], ...] = (
    ("form d", "D"),
    ("reg d", "D"),
    ("13f", "13F-HR"),
    ("schedule 13d", "SC 13D"),
    ("8-k", "8-K"),
    ("10-k", "10-K"),
    ("10-q", "10-Q"),
    ("s-1", "S-1"),
)


# EDGAR FTS ANDs every term against literal document text, so briefs must be
# stripped of instruction/meta words ("show Form D filings from …") before
# they can match anything.
_EDGAR_QUERY_NOISE = frozenset(
    {
        "filing", "filings", "filed", "edgar", "sec", "form", "forms",
        "show", "find", "list", "all", "every", "search", "pull", "get", "track",
        "from", "by", "for", "of", "the", "a", "an", "in", "on", "with", "and",
        "recent", "latest", "new", "this", "year", "quarter", "month", "active",
    }
)


def _edgar_query_ladder(query: str) -> list[str]:
    """Cleaned FTS queries, most-specific first, relaxing until something hits."""
    lowered = query.lower()
    for hint, _form in _EDGAR_FORM_HINTS:
        lowered = lowered.replace(hint, " ")
    words = re.findall(r"[a-z0-9][a-z0-9&.-]*", lowered)
    terms = [word for word in words if word not in _EDGAR_QUERY_NOISE]
    ladder = []
    if terms:
        ladder.append(" ".join(terms))
    if len(terms) > 2:
        ladder.append(" ".join(terms[:2]))
    return ladder or [query]


async def _edgar_search(request: ResearchRequest, settings: Settings) -> list[ResultRow]:
    """SEC EDGAR full-text search — keyless, primary-source filings."""
    base_params: dict[str, str] = {}
    forms = _edgar_forms_filter(request.query)
    if forms:
        base_params["forms"] = forms
    if request.freshness_days:
        today = datetime.now(UTC).date()
        base_params["dateRange"] = "custom"
        base_params["startdt"] = (today - timedelta(days=request.freshness_days)).isoformat()
        base_params["enddt"] = today.isoformat()

    async with httpx.AsyncClient(timeout=20.0) as client:
        for query in _edgar_query_ladder(request.query):
            data = await _edgar_fetch(client, {**base_params, "q": query}, settings)
            rows = _edgar_results_to_rows(data, request)
            if rows:
                if settings.ct_search_edgar_enrich_form_d:
                    hits = (data.get("hits") or {}).get("hits") or []
                    sources = [hit.get("_source") or {} for hit in hits[: len(rows)]]
                    rows = await _enrich_form_d_rows(rows, sources, settings, client)
                return rows
    return []


async def _edgar_fetch(
    client: httpx.AsyncClient, params: dict[str, str], settings: Settings
) -> dict[str, Any]:
    # EDGAR FTS nodes intermittently 500 on valid queries — retry briefly
    # before letting the executor fall back.
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = await client.get(
                EDGAR_FTS_URL,
                params=params,
                headers={"User-Agent": settings.ct_search_edgar_user_agent},
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            last_error = exc
            if exc.response.status_code < 500:
                raise
            await asyncio.sleep(0.4 * (attempt + 1))
    raise last_error if last_error else RuntimeError("EDGAR search failed")


def _edgar_results_to_rows(data: dict[str, Any], request: ResearchRequest) -> list[ResultRow]:
    rows: list[ResultRow] = []
    hits = (data.get("hits") or {}).get("hits") or []
    for hit in hits[: request.max_results]:
        source = hit.get("_source") or {}
        names = [str(name) for name in source.get("display_names") or []]
        company = names[0].split("(CIK")[0].strip() if names else "Unknown filer"
        form = str(source.get("form") or source.get("file_type") or "Filing")
        file_date = str(source.get("file_date") or "")
        ciks = [str(cik) for cik in source.get("ciks") or []]
        url = _edgar_filing_url(ciks[0] if ciks else "", str(source.get("adsh") or ""))
        locations = [str(location) for location in source.get("biz_locations") or []]

        summary_bits = [f"{form} filed {file_date}" if file_date else form]
        if len(names) > 1:
            summary_bits.append(f"{len(names)} related filers")
        if locations:
            summary_bits.append(locations[0])

        rows.append(
            ResultRow(
                fields={
                    "title": f"{form} — {company}",
                    "url": url,
                    "summary": "; ".join(summary_bits),
                    "published_date": file_date,
                },
                confidence=0.92,  # primary-source document, exact-match retrieval
                citations=[Evidence(title=names[0] if names else company, url=url)],
                provider="edgar",
            )
        )
    return rows


def _edgar_filing_url(cik: str, adsh: str) -> str:
    cik_number = cik.lstrip("0")
    accession = adsh.replace("-", "")
    if not cik_number or not accession:
        return "https://www.sec.gov/edgar/search/"
    return f"https://www.sec.gov/Archives/edgar/data/{cik_number}/{accession}/{adsh}-index.htm"


def _edgar_forms_filter(query: str) -> str:
    lowered = query.lower()
    forms = [form for hint, form in _EDGAR_FORM_HINTS if hint in lowered]
    return ",".join(dict.fromkeys(forms))


# --- Form D per-row enrichment (docs/form-d-enrichment-spec.md) ---------------
# FTS returns metadata only; the offering amounts, related persons, and paid
# placement agents live in each filing's structured primary_doc.xml. We parse
# that primary source ourselves rather than route to a secondary aggregator.

_FORM_D_RELATED_PERSON_CAP = 6
# Filers use placeholder tokens for the unused half of an entity's name (an entity
# related person has a last/legal name but no first name) — strip them so a row
# reads "Fairmount GP LLC", not "- Fairmount GP LLC".
_FORM_D_NAME_PLACEHOLDERS = frozenset({"", "-", "--", "n/a", "na", "none", "."})


def _form_d_doc_url(cik: str, adsh: str) -> str:
    """primary_doc.xml URL — same Archives base as the index page, doc instead."""
    cik_number = cik.lstrip("0")
    accession = adsh.replace("-", "")
    if not cik_number or not accession:
        return ""
    return (
        f"https://www.sec.gov/Archives/edgar/data/{cik_number}/{accession}/primary_doc.xml"
    )


def _et_text(node: ET.Element | None, path: str) -> str:
    """Stripped text at `path` under `node`, or "" when absent."""
    if node is None:
        return ""
    found = node.find(path)
    return found.text.strip() if found is not None and found.text else ""


def _form_d_amount(raw: str) -> int | str | None:
    """USD int when numeric; pass through the literal "Indefinite"; else None.

    Form D amounts are open-ended for many pooled funds (totalOfferingAmount =
    "Indefinite"), and "0" is a real "yet to sell" value distinct from missing —
    so this never blindly int()s and never collapses 0 into None.
    """
    if not raw:
        return None
    if raw.strip().lower() == "indefinite":
        return "Indefinite"
    cleaned = raw.replace(",", "").replace("$", "").strip()
    try:
        return int(float(cleaned))
    except ValueError:
        return None


def _form_d_related_persons(root: ET.Element) -> str:
    """GPs / executive officers / directors / promoters, "Name (roles)"-joined."""
    container = root.find("relatedPersonsList")
    if container is None:
        return ""
    people: list[str] = []
    for info in container.findall("relatedPersonInfo")[:_FORM_D_RELATED_PERSON_CAP]:
        first = _et_text(info, "relatedPersonName/firstName")
        last = _et_text(info, "relatedPersonName/lastName")
        parts = [
            part
            for part in (first, last)
            if part and part.lower() not in _FORM_D_NAME_PLACEHOLDERS
        ]
        name = " ".join(parts)
        if not name:
            continue
        rels = [
            rel.text.strip()
            for rel in info.findall("relatedPersonRelationshipList/relationship")
            if rel is not None and rel.text and rel.text.strip()
        ]
        people.append(f"{name} ({', '.join(rels)})" if rels else name)
    return "; ".join(people)


def _form_d_placement_agents(offering: ET.Element) -> str:
    """Paid sales-compensation recipients — placement agents / broker-dealers."""
    container = offering.find("salesCompensationList")
    if container is None:
        return ""
    agents: list[str] = []
    for recipient in container.findall("recipient"):
        name = _et_text(recipient, "recipientName")
        if not name or name.lower() == "none":
            continue
        crd = _et_text(recipient, "recipientCRDNumber")
        if crd and crd.lower() != "none":
            agents.append(f"{name} (CRD {crd})")
        else:
            agents.append(name)
    return "; ".join(agents)


def _parse_form_d(xml_text: str) -> dict[str, Any]:
    """Map a Form D primary_doc.xml to enrichment fields. Pure, never raises.

    Only keys with meaningful values are returned, so a merge never clobbers an
    existing field with an empty string. Field paths verified against a live
    filing, 2026-06-22 (docs/form-d-enrichment-spec.md §3).
    """
    if not xml_text or not xml_text.strip():
        return {}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {}

    details: dict[str, Any] = {}
    offering = root.find("offeringData")
    if offering is not None:
        raised = _form_d_amount(_et_text(offering, "offeringSalesAmounts/totalAmountSold"))
        if raised is not None:
            details["amount_raised"] = raised
        total = _form_d_amount(_et_text(offering, "offeringSalesAmounts/totalOfferingAmount"))
        if total is not None:
            details["total_offering"] = total
        remaining = _form_d_amount(_et_text(offering, "offeringSalesAmounts/totalRemaining"))
        if remaining is not None:
            details["total_remaining"] = remaining
        minimum = _form_d_amount(_et_text(offering, "minimumInvestmentAccepted"))
        if minimum is not None:
            details["min_investment"] = minimum
        is_amend = _et_text(offering, "typeOfFiling/newOrAmendment/isAmendment").lower()
        if is_amend:
            details["new_or_amended"] = "amended" if is_amend == "true" else "new"
        industry = _et_text(offering, "industryGroup/industryGroupType")
        if industry:
            details["industry"] = industry
        investors = _et_text(offering, "investors/totalNumberAlreadyInvested")
        if investors.isdigit():
            details["investor_count"] = int(investors)
        agents = _form_d_placement_agents(offering)
        if agents:
            details["placement_agents"] = agents

    persons = _form_d_related_persons(root)
    if persons:
        details["related_persons"] = persons
    return details


def _merge_form_d_details(row: ResultRow, details: dict[str, Any]) -> None:
    """Fold parsed details onto a row and surface the raise in its summary."""
    row.fields.update(details)
    raised = details.get("amount_raised")
    if raised == 0:
        addition = "yet to sell"
    elif isinstance(raised, int):
        addition = f"raised ${raised:,}"
    else:
        return
    summary = str(row.fields.get("summary") or "")
    row.fields["summary"] = f"{summary}; {addition}" if summary else addition


async def _edgar_doc_fetch(
    client: httpx.AsyncClient, url: str, settings: Settings
) -> str:
    """Fetch one primary_doc.xml. Best-effort: returns "" instead of raising."""
    for attempt in range(2):
        try:
            response = await client.get(
                url, headers={"User-Agent": settings.ct_search_edgar_user_agent}
            )
            response.raise_for_status()
            return response.text
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code < 500:
                return ""  # 403 / 404 — give up quietly, keep the metadata row
            await asyncio.sleep(0.3 * (attempt + 1))
        except httpx.HTTPError:
            await asyncio.sleep(0.3 * (attempt + 1))
    return ""


async def _enrich_form_d_rows(
    rows: list[ResultRow],
    sources: list[dict[str, Any]],
    settings: Settings,
    client: httpx.AsyncClient,
) -> list[ResultRow]:
    """Enrich Form D rows in place from their primary_doc.xml.

    Best-effort and non-blocking: only Form D rows are fetched, under a
    concurrency cap that stays within SEC fair-access limits, and any per-row
    fetch/parse failure leaves that row at its metadata baseline.
    """
    targets: list[ResultRow] = []
    urls: list[str] = []
    for row, source in zip(rows, sources, strict=True):
        form = str(source.get("form") or source.get("file_type") or "")
        if not form.upper().startswith("D"):
            continue
        ciks = [str(cik) for cik in source.get("ciks") or []]
        url = _form_d_doc_url(ciks[0] if ciks else "", str(source.get("adsh") or ""))
        if url:
            targets.append(row)
            urls.append(url)
    if not targets:
        return rows

    semaphore = asyncio.Semaphore(max(1, settings.ct_search_edgar_enrich_concurrency))

    async def _fetch(url: str) -> str:
        async with semaphore:
            return await _edgar_doc_fetch(client, url, settings)

    documents = await asyncio.gather(
        *(_fetch(url) for url in urls), return_exceptions=True
    )

    failed = 0
    for row, document in zip(targets, documents, strict=True):
        if isinstance(document, BaseException) or not document:
            failed += 1
            continue
        details = _parse_form_d(document)
        if details:
            _merge_form_d_details(row, details)
        else:
            failed += 1
    logfire.info("edgar_enrich", attempted=len(targets), failed=failed)
    return rows


async def _parallel_task_enrichment(
    request: ResearchRequest,
    settings: Settings,
    target_rows: list[dict[str, Any]] | None = None,
) -> list[ResultRow]:
    return await asyncio.to_thread(
        _parallel_task_enrichment_sync, request, settings, target_rows
    )


def _parallel_task_enrichment_sync(
    request: ResearchRequest,
    settings: Settings,
    target_rows: list[dict[str, Any]] | None = None,
) -> list[ResultRow]:
    from parallel import Parallel

    client = Parallel(api_key=settings.parallel_api_key)
    fields = request.fields or DEFAULT_FIELDS
    output_schema = {
        "type": "json",
        "json_schema": {
            "type": "object",
            "properties": {field: {"type": "string"} for field in fields},
            "required": fields,
        },
    }
    source_rows = target_rows if target_rows is not None else request.rows
    rows: list[ResultRow] = []
    for input_row in source_rows[:5]:
        task_input = {
            "record": input_row,
            "instruction": request.query
            or "Enrich this private-capital contact record with cited public web research.",
        }
        processor, _reason = _processor_for_request(request)
        task_run = client.task_run.create(
            input=json.dumps(task_input),
            task_spec={"output_schema": output_schema},
            processor=processor,
        )
        result = client.task_run.result(task_run.run_id, api_timeout=900)
        output = _to_mapping(getattr(result, "output", result))
        rows.append(
            ResultRow(
                input=input_row,
                fields={field: output.get(field, "") for field in fields},
                confidence=0.84,
                citations=_basis_to_evidence(output),
                provider="parallel",
            )
        )
    return rows


async def _targeted_search_enrichment(
    spec: ProviderSpec,
    request: ResearchRequest,
    settings: Settings,
    rows_to_enrich: list[dict[str, Any]],
) -> list[ResultRow]:
    """Per-row targeted search using a non-Parallel provider's search API.

    Build a focused query per row, run the provider's search, and capture
    the top result's snippet + citation. Structured fields are left empty
    on purpose — only `source_notes` and `recent_signal` are filled with
    independent excerpts. The merge step will overlay these onto a primary
    row's structured fields without overwriting them.
    """
    fields = request.fields or DEFAULT_FIELDS
    results: list[ResultRow] = []
    for row in rows_to_enrich[:25]:  # bound fallback fan-out
        entity = _entity_label(row, len(results) + 1)
        targeted_query = _targeted_query_for_row(entity, request.query, fields)
        single_request = request.model_copy(
            update={"query": targeted_query, "max_results": 3, "mode": "search"}
        )
        try:
            search_rows = await _run_search(spec, single_request, settings)
        except Exception:
            search_rows = []
        snippet, citations = _summarize_search_rows(search_rows)
        filled = {field: "" for field in fields}
        if "source_notes" in filled:
            filled["source_notes"] = snippet
        if "recent_signal" in filled and snippet:
            filled["recent_signal"] = snippet[:200]
        results.append(
            ResultRow(
                input=row,
                fields=filled,
                confidence=0.55 if snippet else 0.3,
                citations=citations[:3],
                provider=spec.id,
            )
        )
    return results


def _targeted_query_for_row(entity: str, base_query: str, fields: list[str]) -> str:
    field_hint = " ".join(
        field.replace("_", " ") for field in fields if field not in {"source_notes", "firm"}
    )[:80]
    base = base_query.strip() if base_query else ""
    pieces = [piece for piece in (entity, field_hint, base) if piece]
    return " ".join(pieces)[:200]


def _summarize_search_rows(
    search_rows: list[ResultRow],
) -> tuple[str, list[Evidence]]:
    if not search_rows:
        return "", []
    snippets: list[str] = []
    citations: list[Evidence] = []
    for row in search_rows:
        summary = str(row.fields.get("summary") or "")
        if summary:
            snippets.append(_compact(summary, 240))
        citations.extend(row.citations)
    return " | ".join(snippets[:2])[:480], citations


def _parallel_results_to_rows(data: dict[str, Any], request: ResearchRequest) -> list[ResultRow]:
    return [
        _search_result_row(
            provider="parallel",
            query=request.query,
            title=item.get("title", "Untitled result"),
            url=item.get("url", ""),
            summary="\n\n".join(str(part) for part in item.get("excerpts", []) if part),
            published_date=item.get("publish_date") or item.get("published_date") or "",
        )
        for item in data.get("results", [])[: request.max_results]
    ]


def _search_result_row(
    provider: str,
    query: str,
    title: str,
    url: str,
    summary: str,
    published_date: str,
) -> ResultRow:
    return ResultRow(
        input={"query": query},
        fields={
            "title": title,
            "url": url,
            "summary": _compact(summary, 900),
            "published_date": published_date or "",
        },
        confidence=0.78,
        citations=[Evidence(title=title, url=url, excerpt=_compact(summary, 260))] if url else [],
        provider=provider,
    )


def _demo_search(request: ResearchRequest, spec: ProviderSpec) -> list[ResultRow]:
    query = request.query or "private capital placement agent search"
    sample_titles = [
        "Lower-mid-market LP map",
        "Placement agent contact enrichment",
        "Private capital fundraising signal review",
        "CRM gap analysis for capital formation",
    ]
    rows: list[ResultRow] = []
    for index, title in enumerate(sample_titles[: request.max_results], start=1):
        url = f"https://example.com/demo/{_slugify(title)}"
        summary = (
            f"Demo result for '{query}'. Connect {spec.label} credentials to replace this "
            "with live web excerpts, source pages, and provider usage."
        )
        rows.append(
            ResultRow(
                input={"query": query},
                fields={
                    "title": title,
                    "url": url,
                    "summary": summary,
                    "published_date": "",
                },
                confidence=round(0.62 + index * 0.04, 2),
                citations=[Evidence(title=title, url=url, excerpt=summary)],
                provider=spec.id,
            )
        )
    return rows


def _demo_enrichment(request: ResearchRequest, spec: ProviderSpec) -> list[ResultRow]:
    return _demo_enrichment_for(request, spec, request.rows)


def _demo_enrichment_for(
    request: ResearchRequest,
    spec: ProviderSpec,
    target_rows: list[dict[str, Any]],
) -> list[ResultRow]:
    fields = request.fields or DEFAULT_FIELDS
    rows = target_rows or [{"company": "Example Capital", "name": "Sample Contact"}]
    enriched: list[ResultRow] = []
    for index, row in enumerate(rows[:100], start=1):
        entity = _entity_label(row, index)
        values = {field: _demo_value(field, entity, row) for field in fields}
        source_url = f"https://example.com/demo/{_slugify(entity)}"
        enriched.append(
            ResultRow(
                input=row,
                fields=values,
                confidence=_stable_confidence(entity),
                citations=[
                    Evidence(
                        title=f"Demo source for {entity}",
                        url=source_url,
                        excerpt=(
                            f"Demo enrichment generated for {entity}. Add {spec.label} credentials "
                            "for live cited research."
                        ),
                    )
                ],
                provider=spec.id,
            )
        )
    return enriched


def _demo_value(field: str, entity: str, row: dict[str, Any]) -> str:
    normalized = field.lower().replace(" ", "_")
    if normalized in {"firm", "company", "organization"}:
        return str(row.get("company") or row.get("firm") or entity)
    if "role" in normalized or "title" in normalized:
        return str(row.get("title") or row.get("role") or "Needs live verification")
    if "email" in normalized:
        return "Not verified in demo mode"
    if "linkedin" in normalized:
        return "Pending live profile lookup"
    if "sector" in normalized or "focus" in normalized:
        return "Private capital / lower-mid-market signal"
    if "geo" in normalized or "region" in normalized:
        return "US / Europe review candidate"
    if "deal" in normalized or "signal" in normalized:
        return "Recent activity requires live provider verification"
    if "source" in normalized or "note" in normalized:
        return "Demo value; API key required for citations"
    return f"{entity}: pending live {field} research"


def _score_provider(
    spec: ProviderSpec,
    request: ResearchRequest,
    settings: Settings,
    rows: int,
    fields: int,
) -> dict[str, Any]:
    cost = _estimate_cost(spec, request, rows, fields)
    max_cost = max(_estimate_cost(item, request, rows, fields) for item in PROVIDERS)
    cost_score = 1 - (cost / max_cost if max_cost else 0)
    availability_bonus = 0.05 if spec.available(settings) else 0
    prompt_profile = _prompt_profile(request)
    task_fit = _task_fit_score(spec.id, prompt_profile, request)

    if request.routing_mode == "cost":
        score = (
            cost_score * 0.58
            + task_fit * 0.24
            + spec.speed_score * 0.1
            + spec.quality_score * 0.08
        )
    elif request.routing_mode == "speed":
        score = (
            spec.speed_score * 0.48
            + task_fit * 0.3
            + cost_score * 0.12
            + spec.quality_score * 0.1
        )
    elif request.routing_mode == "confidence":
        score = (
            spec.quality_score * 0.36
            + spec.coverage_score * 0.24
            + task_fit * 0.3
            + cost_score * 0.1
        )
    else:
        quality_weight = 0.42 if request.mode == "search" else 0.52
        base_score = (
            spec.quality_score * quality_weight
            + spec.coverage_score * 0.24
            + cost_score * 0.2
            + spec.speed_score * 0.14
        )
        score = base_score * 0.62 + task_fit * 0.38

    # Apply framework adjustments (R3, R4, F1/F2) on top of the base score.
    score *= _source_shape_multiplier(spec.id, request.source_shape)
    score *= _freshness_multiplier(spec.id, request)

    return {
        "id": spec.id,
        "label": spec.label,
        "score": round(score + availability_bonus, 3),
        "task_fit": round(task_fit, 3),
        "estimated_cost": cost,
        "available": spec.available(settings),
        "speed": spec.speed_score,
        "quality": spec.quality_score,
        "coverage": spec.coverage_score,
        "best_for": list(provider_knowledge(spec.id).best_for),
        "tradeoffs": list(provider_knowledge(spec.id).tradeoffs),
    }


def _source_shape_multiplier(provider_id: ProviderId, source_shape: SourceShape) -> float:
    """R3 (similar_to → Exa) and R4 (filings → EDGAR / direct-fetch providers)."""
    if provider_id == "edgar" and source_shape != "filings":
        # Filings-only index: never a contender for open-web shapes.
        return 0.7
    if source_shape == "similar_to":
        # Exa-class semantic providers move to the top regardless of freshness.
        return 1.25 if provider_id == "exa" else 0.85
    if source_shape == "filings":
        # EDGAR is the primary source itself; Parallel direct-fetches regulatory
        # pages; news-wrappers get a soft penalty.
        if provider_id == "edgar":
            return 1.35
        if provider_id == "parallel":
            return 1.15
        if provider_id in ("perplexity", "tavily"):
            return 0.9
    if source_shape == "known_url":
        # Extraction-class workflows favor the venues with wired extract
        # endpoints (Tavily Extract, Exa /contents), then Parallel.
        if provider_id == "tavily":
            return 1.15
        if provider_id == "parallel":
            return 1.1
        if provider_id == "exa":
            return 1.08
    return 1.0


def _freshness_multiplier(provider_id: ProviderId, request: ResearchRequest) -> float:
    """F1 freshness penalty, F2 similar_to override.

    Scales score by clamp(provider_freshness_score, FLOOR, 1.0) when the request
    declares a tight freshness window. Suspended entirely when source_shape is
    similar_to (semantic discovery is not time-bound).
    """
    if request.freshness_days is None:
        return 1.0
    # F2 override
    if request.source_shape == "similar_to":
        return 1.0
    freshness_score = provider_knowledge(provider_id).capability_scores.get("freshness", 0.8)
    # Tighter windows amplify the penalty.
    window_sensitivity = 1.0 if request.freshness_days >= 30 else 1.4
    adjusted = 1.0 - (1.0 - freshness_score) * window_sensitivity
    return max(adjusted, FRESHNESS_PENALTY_FLOOR)


def _prompt_profile(request: ResearchRequest) -> dict[str, bool]:
    text = " ".join(
        [
            request.query,
            " ".join(request.fields or []),
            " ".join(str(value) for row in request.rows[:5] for value in row.values()),
        ]
    ).lower()
    has_rows = bool(request.rows)
    has_fields = bool(request.fields)
    return {
        "needs_enrichment": request.mode == "enrich" or has_rows,
        "needs_structured_output": has_fields
        or _has_any(text, ("json", "csv", "table", "schema", "fields", "columns")),
        "needs_deep_research": _has_any(
            text,
            (
                "compare",
                "landscape",
                "benchmark",
                "diligence",
                "investment memo",
                "comprehensive",
                "multi-hop",
                "deep research",
                "thesis",
            ),
        ),
        "needs_freshness": _has_any(
            text,
            (
                "latest",
                "recent",
                "today",
                "news",
                "announced",
                "funding",
                "raised",
                "signal",
                "current",
            ),
        ),
        "needs_company_people": _has_any(
            text,
            (
                "company",
                "companies",
                "firm",
                "fund",
                "investor",
                "lp",
                "people",
                "contact",
                "linkedin",
                "email",
                "partner",
                "ceo",
            ),
        ),
        "needs_answer_synthesis": _has_any(
            text,
            ("summarize", "brief", "answer", "explain", "memo", "report", "write"),
        ),
        "needs_citations": request.routing_mode == "confidence"
        or _has_any(text, ("cite", "citation", "source", "evidence", "verify", "confidence")),
        "latency_sensitive": request.routing_mode == "speed"
        or _has_any(text, ("fast", "quick", "low latency", "real-time", "realtime")),
        "cost_sensitive": request.routing_mode == "cost"
        or _has_any(text, ("cheap", "budget", "low cost", "cost control")),
    }


def _task_fit_score(
    provider_id: ProviderId,
    prompt_profile: dict[str, bool],
    request: ResearchRequest,
) -> float:
    knowledge = provider_knowledge(provider_id)
    weights = _capability_weights(prompt_profile, request)
    weighted_total = 0.0
    total_weight = 0.0
    for key, weight in weights.items():
        weighted_total += knowledge.capability_scores.get(key, 0.5) * weight
        total_weight += weight
    return weighted_total / total_weight if total_weight else 0.5


def _capability_weights(
    prompt_profile: dict[str, bool],
    request: ResearchRequest,
) -> dict[str, float]:
    weights: dict[str, float] = {
        "raw_search": 0.22,
        "citations": 0.12,
        "freshness": 0.1,
    }
    if prompt_profile["needs_enrichment"]:
        weights.update(
            {
                "structured_enrichment": 0.34,
                "company_people": 0.2,
                "citations": 0.18,
                "content_extraction": 0.08,
            }
        )
    if prompt_profile["needs_structured_output"]:
        weights["structured_enrichment"] = max(weights.get("structured_enrichment", 0), 0.25)
    if prompt_profile["needs_company_people"]:
        weights["company_people"] = max(weights.get("company_people", 0), 0.2)
    if prompt_profile["needs_freshness"]:
        weights["freshness"] = max(weights.get("freshness", 0), 0.22)
    if prompt_profile["needs_deep_research"]:
        weights.update({"deep_research": 0.28, "semantic_discovery": 0.16, "citations": 0.18})
    if prompt_profile["needs_answer_synthesis"]:
        weights["answer_synthesis"] = max(weights.get("answer_synthesis", 0), 0.24)
    if prompt_profile["needs_citations"]:
        weights["citations"] = max(weights.get("citations", 0), 0.26)
    if prompt_profile["latency_sensitive"]:
        weights["latency_control"] = max(weights.get("latency_control", 0), 0.26)
    if prompt_profile["cost_sensitive"] or request.routing_mode == "cost":
        weights["low_cost"] = max(weights.get("low_cost", 0), 0.28)
    if request.source_shape == "filings":
        # R4 — filings jobs rank on the filings axis above all else.
        weights["filings"] = 0.45
    return weights


def _route_strategy(
    request: ResearchRequest,
    prompt_profile: dict[str, bool],
    job_type: JobType | None = None,
    rows: int = 1,
) -> str:
    if request.routing_mode == "manual":
        return "manual"
    # Phase 4 — match compiles to its own per-candidate pipeline (resolve →
    # evidence → judge → score → verify), not the row-merge strategies.
    if job_type == "match":
        return "match_pipeline"
    # R6 — enrichment at scale forces a waterfall regardless of other signals.
    row_count = _request_row_count(request, rows)
    if (
        job_type == "enrich"
        and row_count >= WATERFALL_ROW_THRESHOLD
    ):
        return "waterfall"
    # R8 — brief jobs always retrieve→synthesize.
    if job_type == "brief":
        return "retrieve_then_synthesize"
    if prompt_profile["needs_answer_synthesis"] and not prompt_profile["needs_enrichment"]:
        return "retrieve_then_synthesize"
    if (
        prompt_profile["needs_citations"]
        or request.routing_mode == "confidence"
        or request.evidence_risk == "high"
    ):
        return "primary_with_verification"
    if prompt_profile["latency_sensitive"] or prompt_profile["cost_sensitive"]:
        return "primary_with_fallback"
    return "single_provider"


def _route_steps(
    request: ResearchRequest,
    settings: Settings,
    selected: ProviderSpec,
    considered: list[dict[str, Any]],
    rows: int,
    fields: int,
    prompt_profile: dict[str, bool],
    job_type: JobType | None = None,
) -> list[RouteStep]:
    steps = [
        RouteStep(
            provider=selected.id,
            label=selected.label,
            role="primary",
            reason=_primary_step_reason(selected.id, prompt_profile),
            estimated_cost=_estimate_cost(selected, request, rows, fields),
            available=selected.available(settings),
            estimated_cost_per_grounded_row=_cost_per_grounded_row(
                selected, request, rows, fields
            ),
        )
    ]
    strategy = _route_strategy(request, prompt_profile, job_type, rows)
    if strategy == "manual":
        return steps

    ranked_alternates = [
        _spec(item["id"])
        for item in sorted(considered, key=lambda item: item["score"], reverse=True)
        if item["id"] != selected.id
        and _eligible_for_shape(item["id"], request.source_shape)
    ]

    if strategy == "match_pipeline":
        # Primary gathers per-candidate evidence; a verifier re-checks
        # disqualifiers + top-N candidates when the stakes are high (R1).
        if request.evidence_risk == "high":
            verifier = _best_alternate(ranked_alternates, settings)
            if verifier:
                steps.append(
                    RouteStep(
                        provider=verifier.id,
                        label=verifier.label,
                        role="verification",
                        reason=_secondary_step_reason("verification", verifier.id),
                        trigger=(
                            "Re-gather evidence and re-judge disqualifying criteria and the "
                            "top-ranked candidates before the shortlist is exported."
                        ),
                        estimated_cost=_estimate_cost(verifier, request, rows, fields),
                        available=verifier.available(settings),
                        estimated_cost_per_grounded_row=_cost_per_grounded_row(
                            verifier, request, rows, fields
                        ),
                    )
                )
        return steps

    if strategy == "waterfall":
        # R6 — emit two fallbacks for null/miss recovery, then a verifier when risk demands it.
        fallback_count = 2
        for alternate in ranked_alternates[:fallback_count]:
            steps.append(
                RouteStep(
                    provider=alternate.id,
                    label=alternate.label,
                    role="fallback",
                    reason=_secondary_step_reason("fallback", alternate.id),
                    trigger=(
                        "Re-run rows where the prior step returned nulls, low confidence, "
                        "or thin citations; bounded per-row to control cost."
                    ),
                    estimated_cost=_estimate_cost(alternate, request, rows, fields),
                    available=alternate.available(settings),
                    estimated_cost_per_grounded_row=_cost_per_grounded_row(
                        alternate, request, rows, fields
                    ),
                )
            )
        if request.evidence_risk == "high":
            remaining = [spec for spec in ranked_alternates[fallback_count:] if spec]
            verifier = _best_alternate(remaining, settings) if remaining else None
            if verifier:
                steps.append(
                    RouteStep(
                        provider=verifier.id,
                        label=verifier.label,
                        role="verification",
                        reason=_secondary_step_reason("verification", verifier.id),
                        trigger=(
                            "Independent cross-check on fields with confidence below 0.80 "
                            "or high-impact diligence claims."
                        ),
                        estimated_cost=_estimate_cost(verifier, request, rows, fields),
                        available=verifier.available(settings),
                        estimated_cost_per_grounded_row=_cost_per_grounded_row(
                            verifier, request, rows, fields
                        ),
                    )
                )
        return steps

    if strategy in {"primary_with_fallback", "primary_with_verification"}:
        alternate = _best_alternate(ranked_alternates, settings)
        if alternate:
            role = "verification" if strategy == "primary_with_verification" else "fallback"
            steps.append(
                RouteStep(
                    provider=alternate.id,
                    label=alternate.label,
                    role=role,
                    reason=_secondary_step_reason(role, alternate.id),
                    trigger=(
                        "Use when confidence is below 0.70, citations are thin, "
                        "quota is exhausted, or the primary provider errors."
                    ),
                    estimated_cost=_estimate_cost(alternate, request, rows, fields),
                    available=alternate.available(settings),
                    estimated_cost_per_grounded_row=_cost_per_grounded_row(
                        alternate, request, rows, fields
                    ),
                )
            )

    if strategy == "retrieve_then_synthesize":
        # R1 — high evidence risk keeps the mandatory verifier even on
        # synthesis routes: the brief may only cite what survived the check.
        if request.evidence_risk == "high":
            verifier = _best_alternate(ranked_alternates, settings)
            if verifier:
                steps.append(
                    RouteStep(
                        provider=verifier.id,
                        label=verifier.label,
                        role="verification",
                        reason=_secondary_step_reason("verification", verifier.id),
                        trigger=(
                            "Independent cross-check on low-confidence rows before "
                            "they feed the synthesized brief."
                        ),
                        estimated_cost=_estimate_cost(verifier, request, rows, fields),
                        available=verifier.available(settings),
                        estimated_cost_per_grounded_row=_cost_per_grounded_row(
                            verifier, request, rows, fields
                        ),
                    )
                )
        synthesis = _preferred_provider(("perplexity", "parallel"), selected.id, settings)
        if synthesis:
            steps.append(
                RouteStep(
                    provider=synthesis.id,
                    label=synthesis.label,
                    role="synthesis",
                    reason=_secondary_step_reason("synthesis", synthesis.id),
                    trigger=(
                        "Use after retrieval when the product needs a concise brief "
                        "or narrative answer."
                    ),
                    estimated_cost=_estimate_cost(synthesis, request, rows, fields),
                    available=synthesis.available(settings),
                    estimated_cost_per_grounded_row=_cost_per_grounded_row(
                        synthesis, request, rows, fields
                    ),
                )
            )
    return steps


def _best_alternate(candidates: list[ProviderSpec], settings: Settings) -> ProviderSpec | None:
    available = [candidate for candidate in candidates if candidate.available(settings)]
    return (available or candidates or [None])[0]


def _preferred_provider(
    provider_ids: tuple[ProviderId, ...],
    selected_id: ProviderId,
    settings: Settings,
) -> ProviderSpec | None:
    candidates = [_spec(provider_id) for provider_id in provider_ids if provider_id != selected_id]
    return _best_alternate(candidates, settings)


def _primary_step_reason(provider_id: ProviderId, prompt_profile: dict[str, bool]) -> str:
    knowledge = provider_knowledge(provider_id)
    matched = [item for item, enabled in prompt_profile.items() if enabled]
    if matched:
        matched_text = ", ".join(matched[:3]).replace("_", " ")
        return f"Best fit for {matched_text} based on current provider knowledge."
    return f"Best blended fit. Strongest uses: {', '.join(knowledge.best_for[:2])}."


def _secondary_step_reason(role: str, provider_id: ProviderId) -> str:
    knowledge = provider_knowledge(provider_id)
    if role == "verification":
        return f"Cross-check low-confidence fields with {knowledge.best_for[0]}."
    if role == "synthesis":
        return f"Turn retrieved evidence into a cited brief using {knowledge.best_for[0]}."
    return f"Fallback route for resilience; strongest fit is {knowledge.best_for[0]}."


def _knowledge_sources(steps: list[RouteStep]) -> list[str]:
    sources: list[str] = []
    for step in steps:
        for url in provider_knowledge(step.provider).source_urls:
            if url not in sources:
                sources.append(url)
    return sources


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _estimate_cost(spec: ProviderSpec, request: ResearchRequest, rows: int, fields: int) -> float:
    if request.mode == "search":
        return round(spec.estimated_search_cost, 4)
    if spec.id == "parallel":
        if fields <= 2:
            per_row = 0.005
        elif fields <= 5:
            per_row = 0.01
        elif fields <= 10:
            per_row = 0.025
        else:
            per_row = 0.1
        return round(rows * per_row, 4)
    complexity = max(fields / 6, 1)
    return round(rows * spec.estimated_row_cost * complexity, 4)


def _route_reason(
    mode: str,
    selected: ProviderSpec,
    task_fit: float,
    strategy: str,
) -> str:
    strategy_label = strategy.replace("_", " ")
    if mode == "cost":
        return (
            f"{selected.label} scored best for estimated cost while preserving usable coverage. "
            f"Advisor strategy: {strategy_label}; task fit {task_fit:.0%}."
        )
    if mode == "speed":
        return (
            f"{selected.label} scored best for latency-sensitive desk research. "
            f"Advisor strategy: {strategy_label}; task fit {task_fit:.0%}."
        )
    if mode == "confidence":
        return (
            f"{selected.label} scored best for confidence, coverage, and citation quality. "
            f"Advisor strategy: {strategy_label}; task fit {task_fit:.0%}."
        )
    return (
        f"{selected.label} is the best blended fit across task fit, quality, coverage, speed, "
        f"and cost. Advisor strategy: {strategy_label}; task fit {task_fit:.0%}."
    )


def _derive_search_queries(query: str) -> list[str]:
    cleaned = re.sub(r"\s+", " ", query.strip())
    if not cleaned:
        return ["private capital contacts", "placement agent research"]
    words = [word.strip(" ,.;:()[]{}").lower() for word in cleaned.split()]
    words = [word for word in words if len(word) > 2]
    first = " ".join(words[:6])
    capital_terms = re.findall(r"\b[A-Z][A-Za-z0-9&.-]+(?:\s+[A-Z][A-Za-z0-9&.-]+){0,3}", query)
    second = capital_terms[0].lower() if capital_terms else " ".join(words[-6:])
    capital_words = {"fund", "lp", "investor", "capital", "placement", "private"}
    third = " ".join(word for word in words if word in capital_words)
    queries = [first, second, third or "private capital research"]
    unique: list[str] = []
    for item in queries:
        item = item.strip()
        if item and item not in unique:
            unique.append(item[:80])
    return unique[:3] or [cleaned[:80]]


def _processor_for_fields(fields: list[str]) -> str:
    if len(fields) <= 2:
        return "lite"
    if len(fields) <= 5:
        return "base"
    if len(fields) <= 10:
        return "core"
    return "pro"


# PR2 — depth-aware processor escalation. See docs/decision-framework.md (PR2 phasing).
_PROCESSOR_LADDER: tuple[str, ...] = ("lite", "base", "core", "pro")


def _processor_for_request(request: ResearchRequest) -> tuple[str, str]:
    """Pick a Parallel Task processor tier from field count AND depth signals.

    Returns (tier, reason) — the reason is operator-facing.
    """
    fields = request.fields or DEFAULT_FIELDS
    base_tier = _processor_for_fields(fields)
    profile = _prompt_profile(request)
    bumps = 0
    bump_reasons: list[str] = []
    if profile["needs_deep_research"]:
        bumps += 1
        bump_reasons.append("deep-research prompt signals")
    if request.evidence_risk == "high":
        bumps += 1
        bump_reasons.append("high evidence_risk")
    if request.source_shape == "filings":
        # Filings extraction benefits from a heavier processor.
        bumps += 1
        bump_reasons.append("filings source_shape")
    idx = min(_PROCESSOR_LADDER.index(base_tier) + bumps, len(_PROCESSOR_LADDER) - 1)
    chosen = _PROCESSOR_LADDER[idx]
    if chosen == base_tier:
        reason = f"{chosen} (matched by {len(fields)} field schema)"
    else:
        reason = (
            f"{chosen} (escalated from {base_tier}: {', '.join(bump_reasons)})"
        )
    return chosen, reason


def _provider_per_call_cost(
    spec: ProviderSpec, request: ResearchRequest, rows: int, fields: int
) -> float:
    """Direct API call cost only (search/extraction). Excludes downstream tokens."""
    return _estimate_cost(spec, request, rows, fields)


def _provider_downstream_token_cost(spec: ProviderSpec) -> float:
    """Per-result downstream LLM token cost using economics + assumed token price."""
    economics = provider_knowledge(spec.id).economics
    tokens = economics.avg_tokens_per_result
    return (tokens / 1000.0) * DOWNSTREAM_TOKEN_PRICE_PER_1K_USD


def _cost_per_grounded_row(
    spec: ProviderSpec, request: ResearchRequest, rows: int, fields: int
) -> float:
    """Single-step cost to obtain ONE usable, grounded row from this provider.

    Formula: (per_call_cost / rows + downstream_token_cost) / match_rate.
    Match rate < 1 inflates the apparent cost because some rows yield nothing.
    """
    economics = provider_knowledge(spec.id).economics
    per_call_total = _provider_per_call_cost(spec, request, rows, fields)
    # For enrichment we estimate cost per row; for search the cost is per call.
    if request.mode == "enrich":
        per_row_call = per_call_total / max(rows, 1)
    else:
        per_row_call = per_call_total / max(request.max_results, 1)
    per_row_total = per_row_call + _provider_downstream_token_cost(spec)
    match_rate = max(economics.avg_match_rate, 0.1)  # guard against div-by-zero
    return round(per_row_total / match_rate, 5)


def _plan_cost_per_grounded_row(
    steps: list[RouteStep],
    specs_by_id: dict[ProviderId, ProviderSpec],
) -> float:
    """Sum step costs weighted by the residual miss rate at that step.

    Waterfall logic: step 2 only runs for rows step 1 missed (probability =
    1 − match_rate_1). Step 3 only runs for rows steps 1 AND 2 both missed.
    Verifier steps are independent — they always run on the subset that
    triggered them (we model as 30% of grounded rows).
    """
    if not steps:
        return 0.0
    total = 0.0
    residual = 1.0  # probability a given row is still unanswered
    for step in steps:
        if step.estimated_cost_per_grounded_row is None:
            continue
        spec = specs_by_id.get(step.provider)
        match_rate = (
            provider_knowledge(step.provider).economics.avg_match_rate if spec else 0.65
        )
        if step.role in {"primary", "fallback"}:
            total += step.estimated_cost_per_grounded_row * residual
            residual *= max(1.0 - match_rate, 0.0)
        elif step.role == "verification":
            # Verifier triggers on ~30% of successfully grounded rows.
            grounded_fraction = max(1.0 - residual, 0.0)
            total += step.estimated_cost_per_grounded_row * grounded_fraction * 0.3
        elif step.role == "synthesis":
            # Synthesis runs once per grounded row set; cost is amortized.
            total += step.estimated_cost_per_grounded_row * max(1.0 - residual, 0.0)
    return round(total, 5)


def _enrichment_demo_reason(
    settings: Settings, spec: ProviderSpec, row_count: int
) -> str | None:
    """None when live Task enrichment may run; otherwise why it falls to demo.

    Live enrichment is on by default and guarded by the run budget instead of
    a fixed row cap — at the default $2.00 budget that is roughly 80 rows.
    """
    if spec.id != "parallel" or not spec.available(settings):
        return "Parallel credentials are not configured."
    if not settings.ct_search_live_enrichment:
        return "Live enrichment is disabled (CT_SEARCH_LIVE_ENRICHMENT=0)."
    estimated = spec.estimated_row_cost * max(row_count, 1)
    if estimated > settings.ct_search_max_run_budget_usd:
        return (
            f"Estimated enrichment cost ${estimated:.2f} exceeds the "
            f"${settings.ct_search_max_run_budget_usd:.2f} run budget "
            "(CT_SEARCH_MAX_RUN_BUDGET_USD); returning demo rows instead."
        )
    return None


def _spec(provider_id: ProviderId) -> ProviderSpec:
    return next(spec for spec in PROVIDERS if spec.id == provider_id)


def _env_to_attr(key: str) -> str:
    return key.lower()


def _to_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return {}


def _basis_to_evidence(output: dict[str, Any]) -> list[Evidence]:
    basis = output.get("basis") or output.get("_basis") or []
    if not isinstance(basis, list):
        return []
    evidence: list[Evidence] = []
    for item in basis[:5]:
        if isinstance(item, dict):
            evidence.append(
                Evidence(
                    title=str(item.get("title") or item.get("source") or "Parallel source"),
                    url=str(item.get("url") or ""),
                    excerpt=str(item.get("excerpt") or item.get("quote") or ""),
                )
            )
    return evidence


def _entity_label(row: dict[str, Any], index: int) -> str:
    for key in ("company", "firm", "organization", "name", "contact", "email"):
        value = row.get(key) or row.get(key.title()) or row.get(key.upper())
        if value:
            return str(value)
    values = [str(value) for value in row.values() if value not in (None, "")]
    return values[0] if values else f"Record {index}"


def _enrichment_columns(rows: list[dict[str, Any]], fields: list[str]) -> list[str]:
    input_columns: list[str] = []
    for row in rows[:10]:
        for key in row:
            key = str(key)
            if key not in input_columns:
                input_columns.append(key)
    return [*input_columns, *[field for field in fields if field not in input_columns]]


def _stable_confidence(text: str) -> float:
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
    return round(0.68 + (int(digest[:2], 16) / 255) * 0.22, 2)


def _compact(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "result"
