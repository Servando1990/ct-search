from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from ct_search.models import (
    Evidence,
    ProviderId,
    ProviderPublic,
    ResearchRequest,
    ResearchResponse,
    ResultRow,
    RouteDecision,
    RouteStep,
)
from ct_search.provider_knowledge import KNOWLEDGE_REVIEWED_AT, provider_knowledge
from ct_search.settings import Settings


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
            )
        )
    return public


def choose_provider(request: ResearchRequest, settings: Settings) -> RouteDecision:
    specs = list(PROVIDERS)
    by_id = {spec.id: spec for spec in specs}
    rows = max(len(request.rows), 1)
    fields = max(len(request.fields or DEFAULT_FIELDS), 1)
    prompt_profile = _prompt_profile(request)

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
        )
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
        )

    candidates = [spec for spec in specs if spec.available(settings)] or specs
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
    )
    strategy = _route_strategy(request, prompt_profile)
    reason = _route_reason(request.routing_mode, selected, selected_score["task_fit"], strategy)
    if not any(spec.available(settings) for spec in specs):
        reason += " No provider keys are configured, so the app will run in demo mode."

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
    )


async def run_research(request: ResearchRequest, settings: Settings) -> ResearchResponse:
    started = time.perf_counter()
    route = choose_provider(request, settings)
    spec = _spec(route.provider)
    warnings: list[str] = []
    is_demo = not route.available

    try:
        if request.mode == "enrich":
            rows = await _run_enrichment(spec, request, settings)
            columns = _enrichment_columns(request.rows, request.fields or DEFAULT_FIELDS)
            if not route.available or _enrichment_is_demo(settings, spec, request):
                is_demo = True
        else:
            rows = await _run_search(spec, request, settings)
            columns = ["title", "url", "summary", "published_date"]
            if not route.available:
                is_demo = True
    except Exception as exc:
        warnings.append(f"{spec.label} returned an error, so demo results were generated: {exc}")
        is_demo = True
        rows = (
            _demo_enrichment(request, spec)
            if request.mode == "enrich"
            else _demo_search(request, spec)
        )
        if request.mode == "enrich":
            columns = _enrichment_columns(request.rows, request.fields or DEFAULT_FIELDS)
        else:
            columns = ["title", "url", "summary", "published_date"]

    if is_demo:
        warnings.append(
            "Demo mode: connect provider API keys to replace sample data with live research."
        )

    elapsed_ms = round((time.perf_counter() - started) * 1000)
    return ResearchResponse(
        provider=spec.id,
        provider_label=spec.label,
        route=route,
        rows=rows,
        columns=columns,
        elapsed_ms=elapsed_ms,
        estimated_cost=route.estimated_cost,
        is_demo=is_demo,
        warnings=warnings,
    )


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
) -> list[ResultRow]:
    if (
        spec.id == "parallel"
        and spec.available(settings)
        and settings.ct_search_live_enrichment
        and len(request.rows) <= 5
    ):
        return await _parallel_task_enrichment(request, settings)
    return _demo_enrichment(request, spec)


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
) -> list[ResultRow]:
    return await asyncio.to_thread(_parallel_task_enrichment_sync, request, settings)


def _parallel_task_enrichment_sync(request: ResearchRequest, settings: Settings) -> list[ResultRow]:
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
    rows: list[ResultRow] = []
    for input_row in request.rows[:5]:
        task_input = {
            "record": input_row,
            "instruction": request.query
            or "Enrich this private-capital contact record with cited public web research.",
        }
        task_run = client.task_run.create(
            input=json.dumps(task_input),
            task_spec={"output_schema": output_schema},
            processor=_processor_for_fields(fields),
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
    fields = request.fields or DEFAULT_FIELDS
    rows = request.rows or [{"company": "Example Capital", "name": "Sample Contact"}]
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


def _route_strategy(request: ResearchRequest, prompt_profile: dict[str, bool]) -> str:
    if request.routing_mode == "manual":
        return "manual"
    if prompt_profile["needs_answer_synthesis"] and not prompt_profile["needs_enrichment"]:
        return "retrieve_then_synthesize"
    if prompt_profile["needs_citations"] or request.routing_mode == "confidence":
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
) -> list[RouteStep]:
    steps = [
        RouteStep(
            provider=selected.id,
            label=selected.label,
            role="primary",
            reason=_primary_step_reason(selected.id, prompt_profile),
            estimated_cost=_estimate_cost(selected, request, rows, fields),
            available=selected.available(settings),
        )
    ]
    strategy = _route_strategy(request, prompt_profile)
    if strategy == "manual":
        return steps

    ranked_alternates = [
        _spec(item["id"])
        for item in sorted(considered, key=lambda item: item["score"], reverse=True)
        if item["id"] != selected.id
    ]

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


def _enrichment_is_demo(settings: Settings, spec: ProviderSpec, request: ResearchRequest) -> bool:
    return not (
        spec.id == "parallel"
        and spec.available(settings)
        and settings.ct_search_live_enrichment
        and len(request.rows) <= 5
    )


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
