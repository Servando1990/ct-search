from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    parallel_api_key: str | None = None
    brave_api_key: str | None = None
    exa_api_key: str | None = None
    tavily_api_key: str | None = None
    perplexity_api_key: str | None = None
    # Live per-row enrichment is on by default, guarded by the run budget cap
    # below (set to 0/false to force demo enrichment regardless of keys).
    ct_search_live_enrichment: bool = True
    # Per-run spend ceiling: steps beyond the primary are skipped, and live
    # enrichment falls back to demo, once estimates exceed this.
    ct_search_max_run_budget_usd: float = 2.0
    # LLM intent parsing — maps the brief to routing primitives. Falls back to
    # keyword heuristics when unset, so demo mode needs no key.
    anthropic_api_key: str | None = None
    ct_search_intent_model: str = "claude-opus-4-8"
    # SEC EDGAR full-text search is keyless but requires an identifying
    # User-Agent per SEC fair-access policy.
    ct_search_edgar_user_agent: str = "EdnaSearch/0.1 (servando@controlthrive.com)"
    # Per-row Form D enrichment — parse each filing's primary_doc.xml for offering
    # amounts, related persons, and placement agents (docs/form-d-enrichment-spec.md).
    # Best-effort and bounded by max_results; set False to restore metadata-only rows.
    ct_search_edgar_enrich_form_d: bool = True
    # Parallel primary_doc.xml fetches — kept well under SEC's 10 req/s ceiling.
    ct_search_edgar_enrich_concurrency: int = 5
    # Phase 4 — entity resolution registry anchor (SEC company_tickers.json,
    # keyless). Set to 0 to keep resolution fully offline.
    ct_search_entity_registry: bool = True
    # Phase 4 — LLM fit judge for match runs. Reuses the intent model unless
    # overridden; estimated judge cost per candidate per criterion feeds the
    # pre-run cost surfacing and the budget cap.
    ct_search_judge_model: str = "claude-opus-4-8"
    ct_search_judge_cost_per_criterion_usd: float = 0.0066
    # Browser origins allowed to call the API directly (CORS), comma-separated.
    # Local dev origins are always allowed. In production the frontend usually
    # reaches the API through a same-origin proxy, so CORS is moot — but set this
    # to the deployed frontend URL(s) when the browser calls the API directly,
    # e.g. CT_SEARCH_ALLOWED_ORIGINS=https://search.yoursite.com,https://app.vercel.app
    ct_search_allowed_origins: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def allowed_origins(self) -> list[str]:
        """Local dev origins plus any configured for the deployed frontend."""
        local = ["http://127.0.0.1:3000", "http://localhost:3000"]
        extra = [o.strip() for o in self.ct_search_allowed_origins.split(",") if o.strip()]
        return [*local, *extra]


@lru_cache
def get_settings() -> Settings:
    return Settings()
