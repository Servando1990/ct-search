export type ProviderId = "parallel" | "brave" | "exa" | "tavily" | "perplexity";

export type RoutingMode = "best" | "cost" | "speed" | "confidence" | "manual";

export type ResearchMode = "search" | "enrich";

export type RouteStrategy =
  | "single_provider"
  | "primary_with_fallback"
  | "primary_with_verification"
  | "retrieve_then_synthesize"
  | "manual";

export type RouteStepRole = "primary" | "fallback" | "verification" | "synthesis";

export type CellValue = string | number | boolean | null;

export type InputRow = Record<string, CellValue>;

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
}

export interface RouteStep {
  provider: ProviderId;
  label: string;
  role: RouteStepRole;
  reason: string;
  trigger: string;
  estimated_cost: number;
  available: boolean;
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
}
