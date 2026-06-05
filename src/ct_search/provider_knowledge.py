from __future__ import annotations

from dataclasses import dataclass, field

from ct_search.models import CapabilityMetric, ProviderId

KNOWLEDGE_REVIEWED_AT = "2026-05-21"
# Default expiry: 6 months from the knowledge review date, per the framework doc.
METRIC_EXPIRY_DEFAULT = "2026-11-21"
# Assumed downstream model token price (GPT-4o-class) for cost_per_grounded_row math.
DOWNSTREAM_TOKEN_PRICE_PER_1K_USD = 0.005


@dataclass(frozen=True)
class ProviderEconomics:
    """Per-provider economics used to compute cost_per_grounded_row.

    avg_tokens_per_result — bytes of LLM context consumed per returned row.
        Drives downstream inference cost. Parallel ~918, Tavily ~1928
        [vendor-reported, Parallel "OpenAI vs Parallel vs Exa vs Tavily", 2026-05-27].
    avg_match_rate — probability that a row gets a usable answer in one shot.
        Articles cite 50–75% per-provider; waterfalls compound to push above 85%
        [multi-source, Parallel + amplemarket, 2026].
    """

    avg_tokens_per_result: int = 1100
    avg_match_rate: float = 0.65


@dataclass(frozen=True)
class ProviderKnowledge:
    id: ProviderId
    best_for: tuple[str, ...]
    tradeoffs: tuple[str, ...]
    capability_scores: dict[str, float]
    source_urls: tuple[str, ...]
    benchmark_notes: tuple[str, ...] = ()
    # PR2 — economics + per-axis provenance for UI labelling
    economics: ProviderEconomics = field(default_factory=ProviderEconomics)
    metrics: tuple[CapabilityMetric, ...] = ()


PROVIDER_KNOWLEDGE: dict[ProviderId, ProviderKnowledge] = {
    "parallel": ProviderKnowledge(
        id="parallel",
        best_for=(
            "cited structured enrichment",
            "multi-hop research with processor control",
            "private-capital workflows that need source basis per row",
        ),
        tradeoffs=(
            "Task runs are asynchronous and can take seconds to hours depending on processor",
            "Premium processors can become expensive at high row counts",
        ),
        capability_scores={
            "raw_search": 0.9,
            "answer_synthesis": 0.88,
            "structured_enrichment": 0.96,
            "citations": 0.95,
            "freshness": 0.9,
            "latency_control": 0.74,
            "low_cost": 0.55,
            "company_people": 0.82,
            "deep_research": 0.97,
            "semantic_discovery": 0.86,
            "content_extraction": 0.8,
        },
        source_urls=(
            "https://docs.parallel.ai/getting-started/overview",
            "https://docs.parallel.ai/getting-started/pricing",
            "https://docs.parallel.ai/task-api/guides/choose-a-processor",
            "https://parallel.ai/blog/introducing-parallel",
        ),
        benchmark_notes=(
            "Parallel reports strong BrowseComp and DeepResearch Bench results "
            "for Task API processors.",
        ),
        # Tokens: Parallel ~918 / result vs Tavily ~1928; match rate: enrichment articles
        # cite ~70% per-provider with waterfall pushing higher.
        economics=ProviderEconomics(avg_tokens_per_result=918, avg_match_rate=0.72),
        metrics=(
            CapabilityMetric(
                axis="raw_search",
                score=0.98,
                origin="vendor_reported",
                source_url="https://parallel.ai/articles/which-ai-search-api-has-the-best-recall-and-accuracy",
                source_date="2026-05-25",
                expires_at=METRIC_EXPIRY_DEFAULT,
                confidence=0.6,
                notes="98% SimpleQA at $0.005/req [vendor-reported, Parallel, 2026-05-25]",
            ),
            CapabilityMetric(
                axis="deep_research",
                score=0.97,
                origin="vendor_reported",
                source_url="https://parallel.ai/articles/data-enrichment-api-how-to-choose-implement-and-scale-company-intelligence",
                source_date="2026-05-11",
                expires_at=METRIC_EXPIRY_DEFAULT,
                confidence=0.55,
                notes="62% DeepSearchQA at $100/1K runs [vendor-reported, Parallel, 2026-05-11]",
            ),
            CapabilityMetric(
                axis="citations",
                score=0.95,
                origin="vendor_reported",
                source_url="https://parallel.ai/articles/data-enrichment-tools-are-broken-heres-how-to-build-a-company-database-that-isnt",
                source_date="2026-05-11",
                expires_at=METRIC_EXPIRY_DEFAULT,
                confidence=0.7,
                notes="Per-field citations + confidence in Task API output [vendor-reported]",
            ),
        ),
    ),
    "brave": ProviderKnowledge(
        id="brave",
        best_for=(
            "fast raw web retrieval",
            "fresh broad web and news coverage",
            "low-friction fallback search with LLM-ready context",
        ),
        tradeoffs=(
            "Raw search is not a full enrichment or deep-research workflow by itself",
            "Answers endpoint has lower throughput than Search",
        ),
        capability_scores={
            "raw_search": 0.94,
            "answer_synthesis": 0.75,
            "structured_enrichment": 0.45,
            "citations": 0.66,
            "freshness": 0.93,
            "latency_control": 0.92,
            "low_cost": 0.82,
            "company_people": 0.7,
            "deep_research": 0.45,
            "semantic_discovery": 0.62,
            "content_extraction": 0.65,
        },
        source_urls=(
            "https://brave.com/search/api/",
            "https://api-dashboard.search.brave.com/documentation/pricing",
            "https://github.com/perplexityai/search_evals/",
        ),
        benchmark_notes=(
            "Perplexity's public search_evals repo includes Brave in "
            "cross-provider search benchmarks.",
        ),
        economics=ProviderEconomics(avg_tokens_per_result=1100, avg_match_rate=0.60),
        metrics=(
            CapabilityMetric(
                axis="freshness",
                score=0.93,
                origin="vendor_reported",
                source_url="https://parallel.ai/articles/the-honest-2026-comparison-web-search-apis-for-ai-agents",
                source_date="2026-05-27",
                expires_at=METRIC_EXPIRY_DEFAULT,
                confidence=0.55,
                notes="Independent index, $0.005/req [vendor-reported via Parallel, 2026-05-27]",
            ),
        ),
    ),
    "exa": ProviderKnowledge(
        id="exa",
        best_for=(
            "semantic discovery",
            "company and people search",
            "deep search with structured output and citations",
        ),
        tradeoffs=(
            "Search pricing is higher than simple low-cost retrieval providers",
            "Some benchmark pages are vendor-reported and should be validated with Edna telemetry",
        ),
        capability_scores={
            "raw_search": 0.92,
            "answer_synthesis": 0.88,
            "structured_enrichment": 0.88,
            "citations": 0.9,
            "freshness": 0.86,
            "latency_control": 0.88,
            "low_cost": 0.65,
            "company_people": 0.96,
            "deep_research": 0.9,
            "semantic_discovery": 0.97,
            "content_extraction": 0.88,
        },
        source_urls=(
            "https://exa.ai/pricing",
            "https://exa.ai/docs/reference/search",
            "https://exa.ai/docs/reference/exa-research",
            "https://exa.ai/docs/reference/evaluating-exa-search",
        ),
        benchmark_notes=(
            "Exa publishes evaluation guidance and vendor-reported latency/quality tradeoffs.",
        ),
        economics=ProviderEconomics(avg_tokens_per_result=1300, avg_match_rate=0.65),
        metrics=(
            CapabilityMetric(
                axis="semantic_discovery",
                score=0.97,
                origin="vendor_reported",
                source_url="https://parallel.ai/articles/openai-web-search-vs-parallel-vs-exa-vs-tavily-how-to-choose",
                source_date="2026-05-27",
                expires_at=METRIC_EXPIRY_DEFAULT,
                confidence=0.6,
                notes="SimpleQA 87-91% [vendor-reported via Parallel comparison, 2026-05-27]",
            ),
            CapabilityMetric(
                axis="freshness",
                score=0.24,
                origin="vendor_reported",
                source_url="https://parallel.ai/articles/openai-web-search-vs-parallel-vs-exa-vs-tavily-how-to-choose",
                source_date="2026-05-27",
                expires_at=METRIC_EXPIRY_DEFAULT,
                confidence=0.5,
                notes="FreshQA 24% [vendor-reported via Parallel, 2026-05-27]",
            ),
        ),
    ),
    "tavily": ProviderKnowledge(
        id="tavily",
        best_for=(
            "balanced general-purpose agent search",
            "controllable speed versus relevance search depth",
            "extract/crawl/research workflows with structured output",
        ),
        tradeoffs=(
            "Credit costs vary by search depth and extraction/research shape",
            "People/company-specific search is less specialized than Exa",
        ),
        capability_scores={
            "raw_search": 0.86,
            "answer_synthesis": 0.8,
            "structured_enrichment": 0.83,
            "citations": 0.82,
            "freshness": 0.88,
            "latency_control": 0.86,
            "low_cost": 0.8,
            "company_people": 0.66,
            "deep_research": 0.82,
            "semantic_discovery": 0.72,
            "content_extraction": 0.9,
        },
        source_urls=(
            "https://docs.tavily.com/documentation/api-credits",
            "https://docs.tavily.com/documentation/api-reference/endpoint/search",
            "https://docs.tavily.com/documentation/api-reference/endpoint/research",
            "https://docs.tavily.com/examples/quick-tutorials/research-streaming",
        ),
        # Tavily ~1928 tokens/result per the OpenAI-vs-Parallel-vs-Exa-vs-Tavily article;
        # this materially raises cost_per_grounded_row vs Parallel's ~918.
        economics=ProviderEconomics(avg_tokens_per_result=1928, avg_match_rate=0.62),
        metrics=(
            CapabilityMetric(
                axis="raw_search",
                score=0.93,
                origin="vendor_reported",
                source_url="https://parallel.ai/articles/which-ai-search-api-has-the-best-recall-and-accuracy",
                source_date="2026-05-25",
                expires_at=METRIC_EXPIRY_DEFAULT,
                confidence=0.55,
                notes="SimpleQA 93% [vendor-reported via Parallel, 2026-05-25]",
            ),
        ),
    ),
    "perplexity": ProviderKnowledge(
        id="perplexity",
        best_for=(
            "web-grounded answer synthesis",
            "citation-backed summaries and briefs",
            "quick Q&A where the user wants an answer instead of raw URLs",
        ),
        tradeoffs=(
            "Sonar cost includes token costs plus request fees by search context size",
            "Raw Search API should be used when the product needs ranked results rather than prose",
        ),
        capability_scores={
            "raw_search": 0.87,
            "answer_synthesis": 0.96,
            "structured_enrichment": 0.82,
            "citations": 0.92,
            "freshness": 0.9,
            "latency_control": 0.8,
            "low_cost": 0.6,
            "company_people": 0.72,
            "deep_research": 0.88,
            "semantic_discovery": 0.76,
            "content_extraction": 0.72,
        },
        source_urls=(
            "https://docs.perplexity.ai/docs/search/quickstart",
            "https://docs.perplexity.ai/docs/sonar/quickstart",
            "https://docs.perplexity.ai/docs/getting-started/pricing",
            "https://docs.perplexity.ai/docs/admin/rate-limits-usage-tiers",
            "https://github.com/perplexityai/search_evals/",
        ),
        benchmark_notes=(
            "Perplexity publishes search_evals with Perplexity, Exa, Brave, "
            "and Tavily-backed SERP comparisons.",
        ),
        # Sonar has a 50 req/min cap, which constrains parallelism at scale.
        economics=ProviderEconomics(avg_tokens_per_result=1400, avg_match_rate=0.62),
        metrics=(
            CapabilityMetric(
                axis="raw_search",
                score=0.92,
                origin="vendor_reported",
                source_url="https://parallel.ai/articles/which-ai-search-api-has-the-best-recall-and-accuracy",
                source_date="2026-05-25",
                expires_at=METRIC_EXPIRY_DEFAULT,
                confidence=0.55,
                notes="SimpleQA 92%; 50 req/min cap [vendor-reported via Parallel, 2026-05-25]",
            ),
            CapabilityMetric(
                axis="answer_synthesis",
                score=0.96,
                origin="vendor_reported",
                source_url="https://parallel.ai/articles/openai-web-search-vs-parallel-vs-exa-vs-tavily-how-to-choose",
                source_date="2026-05-27",
                expires_at=METRIC_EXPIRY_DEFAULT,
                confidence=0.6,
                notes="Bundled LLM+search produces narrative answers with inline citations",
            ),
        ),
    ),
}


def provider_knowledge(provider_id: ProviderId) -> ProviderKnowledge:
    return PROVIDER_KNOWLEDGE[provider_id]
