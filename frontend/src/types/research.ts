export type ProviderId = "parallel" | "brave" | "exa" | "tavily" | "perplexity" | "edgar";

export type RoutingMode = "best" | "cost" | "speed" | "confidence" | "manual";

export type ResearchMode = "search" | "enrich";

export type RouteStrategy =
  | "single_provider"
  | "primary_with_fallback"
  | "primary_with_verification"
  | "retrieve_then_synthesize"
  | "manual"
  | "waterfall"
  | "match_pipeline";

export type RouteStepRole = "primary" | "fallback" | "verification" | "synthesis";

// PR1 framework primitives — see docs/decision-framework.md
export type JobType =
  | "discover"
  | "enrich"
  | "research"
  | "monitor"
  | "extract"
  | "brief"
  | "verify"
  | "match";

export type SourceShape =
  | "open_web"
  | "known_url"
  | "similar_to"
  | "serp_vertical"
  | "filings"
  | "event_stream"
  | "static_database";

export type EvidenceRisk = "low" | "medium" | "high";

// How the routing primitives were filled — operator, LLM intent parser, or
// keyword heuristics.
export type IntentOrigin = "operator" | "llm" | "heuristic";

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

// Phase 4 — thesis matching (docs/match-spec.md)
export type ThesisKind = "deal_equity" | "fund_raise" | "sell_side" | "custom";
export type CriterionCall = "pass" | "fail" | "unknown";
export type FitBand = "strong" | "possible" | "weak" | "disqualified";

export interface ThesisCriterion {
  key: string;
  description: string;
  weight: number;
  disqualifying: boolean;
}

export interface Thesis {
  kind: ThesisKind;
  summary: string;
  criteria: ThesisCriterion[];
  sector: string | null;
  geography: string | null;
  check_size_min_usd: number | null;
  check_size_max_usd: number | null;
  structure: string | null;
  timeline: string | null;
  origin: IntentOrigin;
}

export interface CriterionVerdict {
  key: string;
  verdict: CriterionCall;
  confidence: number;
  citations: Evidence[];
  note: string;
  disqualifying: boolean;
}

export interface FitResult {
  fit: number;
  band: FitBand;
  verdicts: CriterionVerdict[];
  disqualifiers: string[];
  known_weight_share: number;
  rationale: string;
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
  // Phase 4 — identity provenance + full fit breakdown for match runs.
  match_basis?: string;
  fit_result?: FitResult | null;
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
  // Intent parsing — how the framework signals were filled.
  intent_origin: IntentOrigin;
  intent_note: string;
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
  // Phase 4 — the thesis a match run scored against (extracted or supplied).
  thesis?: Thesis | null;
}

export interface PreviewResponse {
  filename: string;
  row_count: number;
  columns: string[];
  rows: InputRow[];
}

// Async runs (phase 2)
export type RunStatus = "queued" | "running" | "done" | "error";

export interface RunSummary {
  id: string;
  created_at: string;
  status: RunStatus;
  query: string;
  mode: ResearchMode;
  row_count: number;
  provider?: string | null;
  strategy?: string | null;
  estimated_cost?: number | null;
  is_demo: boolean;
  elapsed_ms?: number | null;
  error?: string | null;
}

export interface RunDetail extends RunSummary {
  response: ResearchResponse | null;
}

export interface RunEvent {
  seq: number;
  kind: string;
  payload: Record<string, unknown>;
}

// Phase 4 — per-row keep/drop on a fit-ranked shortlist.
export interface MatchRowFeedback {
  row_id: string;
  fit_shown?: number | null;
  band_shown?: string | null;
  decision: "kept" | "dropped";
  reason?: string | null;
}

// PR3 — user outcome signals attached to a route plan for prior recalibration.
export interface OutcomePayload {
  accepted_rows?: number | null;
  rejected_rows?: number | null;
  exported?: boolean;
  edited_fields?: number | null;
  // Phase 4 — fit-list feedback feeding calibration.
  match_feedback?: MatchRowFeedback[];
}

// Phase 4 — upload-preview dedupe clustering.
export type MatchLevel = "certain" | "probable" | "review" | "distinct";

export interface DedupeCluster {
  row_indices: number[];
  level: MatchLevel;
  basis: string;
  score: number;
  label: string;
  evidence: string;
}

export interface DedupeResponse {
  rows_hash: string;
  cluster_count: number;
  clusters: DedupeCluster[];
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
  // Phase 4 — optional operator-supplied deal profile; omitted means "extract".
  thesis?: Thesis | null;
}
