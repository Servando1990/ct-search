export type ProviderId = "parallel" | "brave" | "exa" | "tavily" | "perplexity";

export type RoutingMode = "best" | "cost" | "speed" | "confidence" | "manual";

export type ResearchMode = "search" | "enrich";

export type RouteStrategy =
  | "single_provider"
  | "primary_with_fallback"
  | "primary_with_verification"
  | "retrieve_then_synthesize"
  | "manual"
  | "waterfall";

export type RouteStepRole = "primary" | "fallback" | "verification" | "synthesis";

// PR1 framework primitives — see docs/decision-framework.md
export type JobType =
  | "discover"
  | "enrich"
  | "research"
  | "monitor"
  | "extract"
  | "brief"
  | "verify";

export type SourceShape =
  | "open_web"
  | "known_url"
  | "similar_to"
  | "serp_vertical"
  | "filings"
  | "event_stream"
  | "static_database";

export type EvidenceRisk = "low" | "medium" | "high";

export interface ScaleHint {
  rows?: number | null;
  max_budget_usd?: number | null;
}

export type CellValue = string | number | boolean | null;

export type InputRow = Record<string, CellValue>;

export type CapabilityOrigin =
  | "vendor_reported"
  | "internal_eval"
  | "usage_telemetry"
  | "operator_override";

export interface CapabilityMetric {
  axis: string;
  score: number;
  origin: CapabilityOrigin;
  source_url: string;
  source_date: string;
  expires_at: string;
  confidence: number;
  notes: string;
}

export interface ProviderPublic {
  id: ProviderId;
  label: string;
  env_keys: string[];
  strengths: string[];
  estimated_search_cost: number;
  estimated_row_cost: number;
  speed_score: number;
  quality_score: number;
  coverage_score: number;
  available: boolean;
  best_for: string[];
  tradeoffs: string[];
  // PR2 — economics + per-axis provenance
  avg_tokens_per_result: number;
  avg_match_rate: number;
  metrics: CapabilityMetric[];
}

export interface Evidence {
  title: string;
  url: string;
  excerpt: string;
}

export interface ResultRow {
  input: InputRow;
  fields: Record<string, CellValue>;
  confidence: number;
  citations: Evidence[];
  provider: string;
  // PR4 — per-row attribution from the executed plan.
  step_role: string;
  verified: boolean;
  contributing_providers: string[];
}

export interface RouteDecision {
  provider: ProviderId;
  label: string;
  routing_mode: RoutingMode;
  strategy: RouteStrategy;
  reason: string;
  score: number;
  estimated_cost: number;
  available: boolean;
  considered: Array<{
    id: ProviderId;
    label: string;
    score: number;
    estimated_cost: number;
    available: boolean;
    speed: number;
    quality: number;
    coverage: number;
    task_fit?: number;
    best_for?: string[];
    tradeoffs?: string[];
  }>;
  steps: RouteStep[];
  prompt_profile: Record<string, boolean>;
  knowledge_version: string;
  knowledge_sources: string[];
  // PR1 framework signals
  job_type: JobType | null;
  source_shape: SourceShape;
  evidence_risk: EvidenceRisk;
  freshness_days: number | null;
  caveats: string[];
  // PR2 — true plan cost + Parallel processor escalation
  estimated_cost_per_grounded_row: number | null;
  processor_tier: string | null;
  processor_reason: string;
}

export interface RouteStep {
  provider: ProviderId;
  label: string;
  role: RouteStepRole;
  reason: string;
  trigger: string;
  estimated_cost: number;
  available: boolean;
  estimated_cost_per_grounded_row: number | null;
}

export interface ResearchResponse {
  provider: ProviderId;
  provider_label: string;
  route: RouteDecision;
  rows: ResultRow[];
  columns: string[];
  elapsed_ms: number;
  estimated_cost: number;
  is_demo: boolean;
  warnings: string[];
  // PR3 — link to telemetry row for user_outcome attachment.
  route_plan_id: string;
}

export interface PreviewResponse {
  filename: string;
  row_count: number;
  columns: string[];
  rows: InputRow[];
}

export interface ResearchPayload {
  mode: ResearchMode;
  query: string;
  rows: InputRow[];
  fields: string[];
  routing_mode: RoutingMode;
  provider: ProviderId | null;
  max_results: number;
  // PR1 framework — optional, omitted fields fall back to backend defaults
  job_type?: JobType | null;
  source_shape?: SourceShape;
  evidence_risk?: EvidenceRisk;
  freshness_days?: number | null;
  scale_hint?: ScaleHint | null;
}
