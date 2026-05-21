from __future__ import annotations

from dataclasses import dataclass

from ct_search.models import ProviderId

KNOWLEDGE_REVIEWED_AT = "2026-05-21"


@dataclass(frozen=True)
class ProviderKnowledge:
    id: ProviderId
    best_for: tuple[str, ...]
    tradeoffs: tuple[str, ...]
    capability_scores: dict[str, float]
    source_urls: tuple[str, ...]
    benchmark_notes: tuple[str, ...] = ()


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
    ),
}


def provider_knowledge(provider_id: ProviderId) -> ProviderKnowledge:
    return PROVIDER_KNOWLEDGE[provider_id]
