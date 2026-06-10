from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    parallel_api_key: str | None = None
    brave_api_key: str | None = None
    exa_api_key: str | None = None
    tavily_api_key: str | None = None
    perplexity_api_key: str | None = None
    ct_search_live_enrichment: bool = False
    # LLM intent parsing — maps the brief to routing primitives. Falls back to
    # keyword heuristics when unset, so demo mode needs no key.
    anthropic_api_key: str | None = None
    ct_search_intent_model: str = "claude-opus-4-8"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
