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
]
RouteStepRole = Literal["primary", "fallback", "verification", "synthesis"]


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


class ResearchRequest(BaseModel):
    mode: ResearchMode = "search"
    query: str = Field(default="", max_length=4000)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    fields: list[str] = Field(default_factory=list)
    routing_mode: RoutingMode = "best"
    provider: ProviderId | None = None
    max_results: int = Field(default=8, ge=1, le=25)


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


class RouteStep(BaseModel):
    provider: ProviderId
    label: str
    role: RouteStepRole
    reason: str
    trigger: str = ""
    estimated_cost: float
    available: bool


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


class ExportRequest(BaseModel):
    title: str = "Edna Search Results"
    columns: list[str] = Field(default_factory=list)
    rows: list[ResultRow] = Field(default_factory=list)
    route: RouteDecision | None = None
