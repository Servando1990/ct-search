# Provider Routing Advisor

> **Historical design record** (researched 2026-05-21). Kept for the provider
> evidence and pricing citations. The routing system has since moved well past
> this plan — multi-step execution, intent parsing, async runs, budget caps,
> EDGAR, and calibration are all live. Current truth lives in
> [decision-framework.md](decision-framework.md) (rules) and
> [spec.md](spec.md) (status).

This document records the first implementation plan for routing Edna Search jobs to either a
single vendor or a small vendor mix based on the user's prompt, uploaded rows, requested fields,
cost/speed/confidence preference, and available credentials.

## Goal

Edna Search should not treat every vendor as the same shape of search API. A placement-agent
workflow can need fast raw retrieval, cited row enrichment, company/person discovery, answer
synthesis, or deep multi-hop research. The advisor should classify the job, score vendors against
the task, and return an auditable route plan.

The first implementation keeps execution on one primary provider for compatibility, but it now
returns a structured route plan with primary, fallback, verification, or synthesis steps.

## Current Provider Evidence

Parallel is strongest for cited structured enrichment and multi-hop research. Its docs position
Search as one-trip web grounding and Task API as multi-hop cited research; Task processors range
from `lite` through `ultra8x`, with fast variants and per-task-run pricing rather than per output
field ([Parallel overview](https://docs.parallel.ai/getting-started/overview),
[Parallel pricing](https://docs.parallel.ai/getting-started/pricing),
[Parallel processors](https://docs.parallel.ai/task-api/guides/choose-a-processor)). Parallel also
publishes benchmark claims for BrowseComp and DeepResearch Bench, but those should be treated as
vendor-reported until Edna has its own telemetry ([Parallel benchmark post](https://parallel.ai/blog/introducing-parallel)).

Brave is strongest as fast broad web retrieval and resilient fallback. Brave describes its API as
running on an independent web index, offers Search at $5 per 1,000 requests, includes LLM context,
and lists 50 QPS capacity for Search ([Brave Search API](https://brave.com/search/api/),
[Brave pricing](https://api-dashboard.search.brave.com/documentation/pricing)). It is weaker as a
standalone structured enrichment engine.

Exa is strongest for semantic discovery, company/person search, and structured deep search. Exa's
pricing page lists Search at $7 per 1,000 requests, Deep Search at $12-15 per 1,000 requests, Agent
at $0.025-$2.00 per run, and people/company use cases ([Exa pricing](https://exa.ai/pricing)).
Its docs describe Search with contents/highlights and Research with structured JSON and citations
([Exa Search](https://exa.ai/docs/reference/search),
[Exa Research](https://exa.ai/docs/reference/exa-research)). Exa's evaluation material is useful
for task-fit assumptions but still vendor-authored ([Exa evaluation guide](https://exa.ai/docs/reference/evaluating-exa-search)).

Tavily is strongest for balanced agent search, extraction, crawl/map workflows, and controllable
search depth. Tavily's docs list 1,000 free credits/month, pay-as-you-go at $0.008 per credit,
basic search at 1 credit, advanced search at 2 credits, and extract/crawl pricing by depth and
successful pages ([Tavily credits](https://docs.tavily.com/documentation/api-credits),
[Tavily Search](https://docs.tavily.com/documentation/api-reference/endpoint/search)). Tavily
Research supports `mini`, `pro`, and `auto`, plus structured output and citations
([Tavily Research](https://docs.tavily.com/documentation/api-reference/endpoint/research),
[Tavily streaming research](https://docs.tavily.com/examples/quick-tutorials/research-streaming)).

Perplexity is strongest when the user wants a direct web-grounded answer or cited narrative brief.
Perplexity now separates raw Search API from Sonar and Agent APIs. The Search API is $5 per 1,000
requests with no token costs, while Sonar combines token costs with request fees by search context
size ([Perplexity pricing](https://docs.perplexity.ai/docs/getting-started/pricing)). Sonar is
documented as a fast, real-time web-search answer model with citations
([Perplexity Sonar](https://docs.perplexity.ai/docs/sonar/models/sonar)). Perplexity publishes
`search_evals`, a public benchmark framework covering Perplexity, Exa, Brave, and Tavily-backed
SERP search across suites such as SimpleQA, FRAMES, BrowseComp, DSQA, HLE, and SEAL
([Perplexity search_evals](https://github.com/perplexityai/search_evals/)).

## Routing Shape

The advisor classifies prompt and row context into signals:

- `needs_enrichment`
- `needs_structured_output`
- `needs_deep_research`
- `needs_freshness`
- `needs_company_people`
- `needs_answer_synthesis`
- `needs_citations`
- `latency_sensitive`
- `cost_sensitive`

Those signals become weighted capability requirements. The provider score combines the existing
cost/speed/quality/coverage score with provider capability fit from
`src/ct_search/provider_knowledge.py`.

## Strategies

`single_provider`: Use when one provider fits and the job does not ask for explicit verification,
synthesis, cost fallback, or speed fallback.

`primary_with_fallback`: Use when the user asks for speed or cost control. The secondary provider
should trigger on quota exhaustion, provider error, or low-confidence result rows.

`primary_with_verification`: Use when the user asks for confidence, citations, sources, or
verification. The secondary provider should cross-check thin citations or low-confidence fields.

`retrieve_then_synthesize`: Use when the request is primarily an answer/brief/report job. Retrieval
can come from the best raw-search provider, while synthesis should favor Perplexity or Parallel.

`manual`: Preserve the user's selected provider, while still exposing advisor task fit.

## Implementation Notes

The first branch adds:

- Versioned provider capability cards in `src/ct_search/provider_knowledge.py`.
- Additional route decision fields: `strategy`, `steps`, `prompt_profile`, `knowledge_version`,
  and `knowledge_sources`.
- Task-fit scoring inside `choose_provider`.
- A workbench route-plan panel so operators can see why a run was routed.

The first branch does not yet fan out execution to multiple providers. That should be the next
backend step after adding persistence for run telemetry, because multi-provider execution needs
budget caps, retry rules, confidence thresholds, and export provenance.

## Source Artifacts

The web-search artifacts used during this update are saved locally for follow-up inspection:

- `/tmp/parallel-provider-research.json`
- `/tmp/search-provider-research.json`
- `/tmp/perplexity-provider-research.json`
