from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from ct_search.intent import resolve_intent
from ct_search.models import (
    Evidence,
    JobType,
    ProviderId,
    ProviderPublic,
    ResearchRequest,
    ResearchResponse,
    ResultRow,
    RouteDecision,
    RouteStep,
    SourceShape,
)
from ct_search.provider_knowledge import (
    DOWNSTREAM_TOKEN_PRICE_PER_1K_USD,
    KNOWLEDGE_REVIEWED_AT,
    provider_knowledge,
)
from ct_search.settings import Settings
from ct_search.telemetry import (
    StepResult,
    log_route_plan,
    new_route_plan_id,
)

# Decision-framework thresholds — see docs/decision-framework.md
WATERFALL_ROW_THRESHOLD = 50  # R6: enrich at scale forces waterfall
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
    caveats = _framework_caveats(request, job_type, rows)

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
    # Back-compat inference from the legacy `mode` field.
    if request.mode == "enrich":
        return "enrich"
    # mode == "search": pick the closest job_type from prompt signals.
    text = " ".join([request.query or "", " ".join(request.fields or [])]).lower()
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


def _framework_caveats(
    request: ResearchRequest, job_type: JobType, rows: int
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
    return caveats


def _apply_framework_filters(
    specs: list[ProviderSpec],
    request: ResearchRequest,
    job_type: JobType,
    caveats: list[str],
) -> list[ProviderSpec]:
    """Apply R1 (evidence-risk floor) and R3 (similar_to override) to candidate set."""
    candidates = list(specs)

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

    for index, step in enumerate(route.steps):
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
    )


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

    Match by `input` row identity (the original input dict). Preserves
    primary's provider attribution for its non-blank fields; appends
    supplement provider to `contributing_providers` when any field was filled.
    """
    supplement_index = {_input_key(row.input): row for row in supplement}
    merged: list[ResultRow] = []
    for row in primary:
        key = _input_key(row.input)
        supp = supplement_index.get(key)
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
    """Mark rows verified when an independent provider agrees on key fields."""
    by_key = {_input_key(row.input): row for row in verified_rows}
    updated: list[ResultRow] = []
    for row in rows:
        verifier = by_key.get(_input_key(row.input))
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
    payload = {
        "model": "sonar",
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
    """R3 (similar_to → Exa) and R4 (filings → direct-fetch providers)."""
    if source_shape == "similar_to":
        # Exa-class semantic providers move to the top regardless of freshness.
        return 1.25 if provider_id == "exa" else 0.85
    if source_shape == "filings":
        # Parallel directly fetches SEC/regulatory; news-wrappers get a soft penalty.
        if provider_id == "parallel":
            return 1.15
        if provider_id in ("perplexity", "tavily"):
            return 0.9
    if source_shape == "known_url":
        # Extraction-class workflows favor Parallel (Extract) / Tavily (extract endpoint).
        if provider_id in ("parallel", "tavily"):
            return 1.1
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
    return weights


def _route_strategy(
    request: ResearchRequest,
    prompt_profile: dict[str, bool],
    job_type: JobType | None = None,
    rows: int = 1,
) -> str:
    if request.routing_mode == "manual":
        return "manual"
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
    ]

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
