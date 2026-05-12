export type ProviderId = "parallel" | "brave" | "exa" | "tavily" | "perplexity";

export type RoutingMode = "best" | "cost" | "speed" | "confidence" | "manual";

export type ResearchMode = "search" | "enrich";

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
  }>;
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
