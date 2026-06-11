from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ProviderId = Literal["parallel", "brave", "exa", "tavily", "perplexity"]
RoutingMode = Literal["best", "cost", "speed", "confidence", "manual"]
ResearchMode = Literal["search", "enrich"]
RouteStrategy = Literal[
    "single_provider",
    "primary_with_fallback",
    "primary_with_verification",
    "retrieve_then_synthesize",
    "manual",
    "waterfall",
]
RouteStepRole = Literal["primary", "fallback", "verification", "synthesis"]

# PR1 primitives — see docs/decision-framework.md
JobType = Literal[
    "discover", "enrich", "research", "monitor", "extract", "brief", "verify"
]
SourceShape = Literal[
    "open_web",
    "known_url",
    "similar_to",
    "serp_vertical",
    "filings",
    "event_stream",
    "static_database",
]
EvidenceRisk = Literal["low", "medium", "high"]
# How the routing primitives were filled: set by the operator, inferred by the
# LLM intent parser (intent.py), or left to the keyword heuristics.
IntentOrigin = Literal["operator", "llm", "heuristic"]


class ScaleHint(BaseModel):
    rows: int | None = None
    max_budget_usd: float | None = None


CapabilityOrigin = Literal[
    "vendor_reported", "internal_eval", "usage_telemetry", "operator_override"
]


class CapabilityMetric(BaseModel):
    """A capability score with provenance, expiry, and confidence in the score itself.

    See docs/decision-framework.md — "CapabilityScore" in the data model.
    """

    axis: str
    score: float = Field(ge=0.0, le=1.0)
    origin: CapabilityOrigin = "vendor_reported"
    source_url: str = ""
    source_date: str = ""  # ISO date
    expires_at: str = ""  # ISO date
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    notes: str = ""


class Evidence(BaseModel):
    title: str = ""
    url: str = ""
    excerpt: str = ""


class ResultRow(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)
    fields: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0
    citations: list[Evidence] = Field(default_factory=list)
    provider: str = ""
    # PR4 — per-row attribution from the executed plan.
    step_role: str = ""  # "primary" | "fallback" | "verified" | "synthesized"
    verified: bool = False  # set when a verifier step corroborated this row
    contributing_providers: list[str] = Field(default_factory=list)


class ResearchRequest(BaseModel):
    mode: ResearchMode = "search"
    query: str = Field(default="", max_length=4000)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    fields: list[str] = Field(default_factory=list)
    routing_mode: RoutingMode = "best"
    provider: ProviderId | None = None
    max_results: int = Field(default=8, ge=1, le=25)
    # PR1 primitives — optional; None/default means "infer" (intent.py), and
    # the router treats a missing evidence_risk as "medium".
    job_type: JobType | None = None
    source_shape: SourceShape = "open_web"
    evidence_risk: EvidenceRisk | None = None
    freshness_days: int | None = Field(default=None, ge=0, le=3650)
    scale_hint: ScaleHint | None = None


class ProviderPublic(BaseModel):
    id: ProviderId
    label: str
    env_keys: list[str]
    strengths: list[str]
    estimated_search_cost: float
    estimated_row_cost: float
    speed_score: float
    quality_score: float
    coverage_score: float
    available: bool
    best_for: list[str] = Field(default_factory=list)
    tradeoffs: list[str] = Field(default_factory=list)
    # PR2 — economics + provenance for cost-per-grounded-row + UI provenance labels
    avg_tokens_per_result: int = 1100
    avg_match_rate: float = 0.65
    metrics: list[CapabilityMetric] = Field(default_factory=list)


class RouteStep(BaseModel):
    provider: ProviderId
    label: str
    role: RouteStepRole
    reason: str
    trigger: str = ""
    estimated_cost: float
    available: bool
    # PR2 — true cost including downstream tokens & miss-rate adjustment
    estimated_cost_per_grounded_row: float | None = None


class RouteDecision(BaseModel):
    provider: ProviderId
    label: str
    routing_mode: RoutingMode
    strategy: RouteStrategy = "single_provider"
    reason: str
    score: float
    estimated_cost: float
    available: bool
    considered: list[dict[str, Any]]
    steps: list[RouteStep] = Field(default_factory=list)
    prompt_profile: dict[str, bool] = Field(default_factory=dict)
    knowledge_version: str = ""
    knowledge_sources: list[str] = Field(default_factory=list)
    # PR1 — framework signals surfaced on the route plan
    job_type: JobType | None = None
    source_shape: SourceShape = "open_web"
    evidence_risk: EvidenceRisk = "medium"
    freshness_days: int | None = None
    caveats: list[str] = Field(default_factory=list)
    # Intent parsing — how the framework signals above were filled.
    intent_origin: IntentOrigin = "heuristic"
    intent_note: str = ""
    # PR2 — true plan cost (sum of grounded-row cost across steps, with miss-rate decay)
    estimated_cost_per_grounded_row: float | None = None
    processor_tier: str | None = None  # lite | base | core | pro (when Parallel-driven)
    processor_reason: str = ""


class ResearchResponse(BaseModel):
    provider: ProviderId
    provider_label: str
    route: RouteDecision
    rows: list[ResultRow]
    columns: list[str]
    elapsed_ms: int
    estimated_cost: float
    is_demo: bool = False
    warnings: list[str] = Field(default_factory=list)
    # PR3 — link to telemetry row for user_outcome attachment.
    route_plan_id: str = ""


class ExportRequest(BaseModel):
    title: str = "Edna Search Results"
    columns: list[str] = Field(default_factory=list)
    rows: list[ResultRow] = Field(default_factory=list)
    route: RouteDecision | None = None


# --- Async runs (phase 2) ----------------------------------------------------

RunStatus = Literal["queued", "running", "done", "error"]


class RunSummary(BaseModel):
    id: str
    created_at: str
    status: RunStatus
    query: str
    mode: ResearchMode
    row_count: int = 0
    provider: str | None = None
    strategy: str | None = None
    estimated_cost: float | None = None
    is_demo: bool = False
    elapsed_ms: int | None = None
    error: str | None = None


class RunDetail(RunSummary):
    response: ResearchResponse | None = None
