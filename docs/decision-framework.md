# Edna Search — Decision Framework

This is the canonical mental model the routing layer is built against. It supersedes ad-hoc routing rules in code review. When the code and this document disagree, fix the code or update this document — never silently diverge.

Status: **PR1 + PR2 + PR3 + PR4 shipped.** PR1 implements the request primitives, evidence-risk floor, source-shape gating, freshness penalty, and waterfall emission. PR2 adds capability-score provenance, cost-per-grounded-row (with miss-rate decay across waterfall steps and downstream-token cost), and depth-aware Parallel processor escalation. PR3 wires Logfire-instrumented telemetry, a JSONL calibration sink, the `/api/telemetry/outcome` hook, an Edna-native eval harness, and the nightly score-recompute job. UI shows per-axis `[origin, score]` provenance chips with stale-prior badges. **PR4 makes the plan real**: the executor walks every `route.steps` entry (primary → fallback → verifier → synthesis), runs non-Parallel enrichment via targeted per-row search, merges results without overwriting primary values, marks rows `verified` when an independent provider agrees, and surfaces per-row `via {provider} · {step_role}` attribution in the table and CSV/PDF exports.

## Thesis

Edna's router does not pick the best vendor. It picks the **research route** that matches the job's failure cost. Missing a target in sourcing is recoverable; citing an unverifiable fact in an IC memo is not. The router conditions on **what the user is doing** (`job_type`), **where the answer lives** (`source_shape`), and **what happens if we're wrong** (`evidence_risk`), then emits an ordered **route plan**. Vendor capability is a prior with provenance and expiry, calibrated by Edna's own telemetry — never routing law.

## Scope honesty

Edna is **not** a Preqin / PitchBook / Crunchbase / SEC EDGAR / CRM replacement. It does not own baseline firmographics, fund vintage, AUM, ownership, or contact identity. Edna is the **live-web augmentation layer** that fills the columns those systems can't: recent fundraising signals, hiring velocity, sector positioning, IR contact freshness, diligence context, and export-ready cited evidence for placement-agent and capital-formation workflows.

## Definitions

| Axis | Values | What it controls |
|---|---|---|
| `job_type` | `discover` · `enrich` · `research` · `monitor` · `extract` · `brief` · `verify` | Which architecture class is eligible. |
| `source_shape` | `open_web` · `known_url` · `similar_to` · `serp_vertical` · `filings` · `event_stream` · `static_database` | Which provider class can physically see the answer. |
| `evidence_risk` | `low` · `medium` · `high` | Required audit standard. `low` = desk scan. `medium` = citations required. `high` = per-field citations + confidence + verifier mandatory. |
| `freshness_days` | `int \| null` | Stale-tolerance window. Drives ranking penalty, not exclusion (except via R3 override). |
| `scale_hint` | `{ rows?: int, max_budget_usd?: float }` | Triggers waterfall vs single-shot. `rows >= 50` for `enrich` forces a waterfall plan because per-vendor match rates are bounded ~50–75% [vendor-reported, multi-source, 2026]. |

## Routing decision flow

```
        ResearchRequest
        ├─ job_type, source_shape, evidence_risk
        ├─ freshness_days, scale_hint
        └─ inputs
                │
                ▼
   (1) EVIDENCE-RISK FLOOR
        high   → require citations + verifier
        medium → require citations
        low    → no audit requirement
                │
                ▼
   (2) ARCHITECTURE FILTER
        (job_type × source_shape) → eligible provider classes
        Out-of-scope source_shape → fail loudly with caveat
                │
                ▼
   (3) FRESHNESS PENALTY (not exclusion)
        score *= clamp(1 − max(0, age/freshness_days), 0.2, 1.0)
        Override: source_shape == similar_to suspends penalty
                │
                ▼
   (4) RANK by task-conditional capability mask
        × constraint axis (cost / speed / coverage / quality)
                │
                ▼
   (5) EMIT RoutePlan (ordered):
        cheap_scan → targeted_enrich → verify_subset → synthesize
        waterfall fallbacks when scale_hint.rows ≥ 50
                │
                ▼
   (6) LOG telemetry for calibration loop  [PR3]
```

## Routing policy (ordered, testable rules)

Each rule has an explicit trigger so it can be unit-tested.

- **R1.** `evidence_risk == "high"` AND no eligible provider supports citations + confidence ⇒ fail loudly with an actionable error. Do not silently fall back.
- **R2.** `source_shape == "serp_vertical"` ⇒ only SERP-class providers are eligible as primary. Today's vendor set has none — surface this as a clear caveat in the route reason.
- **R3.** `source_shape == "similar_to"` ⇒ semantic providers (Exa-class) move to the top of the ranking regardless of freshness. Resolves the Exa FreshQA tension.
- **R4.** `source_shape == "filings"` ⇒ prefer providers that fetch SEC/regulatory directly over news-wrapping providers [vendor-reported, Parallel, 2026-05].
- **R5.** `job_type == "discover"` with `source_shape == "open_web"` ⇒ FindAll-class (Parallel) is the only eligible primary. Static databases and SERP APIs cannot answer "companies that did X in last N months."
- **R6.** `job_type == "enrich"` AND `scale_hint.rows >= 50` ⇒ the route plan must include ≥1 fallback step (waterfall), motivated by per-provider match-rate ceiling.
- **R7.** `job_type == "monitor"` ⇒ route through fresh-raw-search providers; expensive deep-research processors are ineligible (wrong tool, wrong economics).
- **R8.** `job_type == "brief"` ⇒ emit `retrieve_then_synthesize` with a different vendor on each leg (diversifies failure mode).
- **F1.** Freshness penalty: ranking weight `*= clamp(1 - max(0, target_age_days / freshness_days), 0.2, 1.0)`. Applies only when `freshness_days` is set.
- **F2.** Override: `source_shape == "similar_to"` or user explicitly chose semantic discovery suspends the freshness penalty (Exa's FreshQA 24% [vendor-reported, Parallel, 2026-05] does not apply when the job isn't time-bound).
- **C1.** Cost ranking uses `cost_per_grounded_row` (search + extraction + downstream tokens + miss-rate-adjusted fallback + verifier-triggered cost), not per-request price. _Implemented in PR2_: each `RouteStep` and the overall `RoutePlan` carry `estimated_cost_per_grounded_row`; waterfall plans accumulate with residual-miss-rate decay so fallback costs are weighted by `(1 − match_rate)^n`. Verifier steps weighted at ~30% of grounded rows; synthesis steps amortized once per grounded set.

## Vendor / provider role taxonomy

Different layers, mostly complementary, NOT substitutable.

| Layer | Vendor | Strongest `(job_type, source_shape, evidence_risk)` |
|---|---|---|
| Entity discovery | Parallel FindAll | `(discover, open_web, medium-high)` |
| AI-native search (proprietary index) | Parallel Search | `(research, open_web, low-medium)`, `(monitor, open_web, medium)` |
| Semantic / neural index | Exa | `(discover, similar_to, *)`, `(brief, similar_to, medium)` |
| RAG-shaped aggregation | Tavily | `(brief, open_web, low)` prototyping |
| Independent SERP-style web | Brave | `(monitor, open_web, low-medium)`, fallback role |
| Multi-engine SERP scraper | SerpAPI-class (not wired) | `(extract, serp_vertical, *)` |
| Bundled search + LLM | Perplexity Sonar | `(brief, open_web, low-medium)` synthesis leg only |
| Agentic multi-hop | Parallel Task | `(enrich/research/verify, *, medium-high)` |
| URL → structured extraction | Parallel Extract (not wired) | `(extract, known_url, *)` |
| Event monitoring | Parallel Monitor (not wired) | `(monitor, event_stream, *)` |
| Static enrichment DB | PitchBook/Preqin/Crunchbase | Out of Edna's scope |

### Vendor-reported priors

These seed the capability scores. **All are vendor-published and must be labelled as such in the UI.** Expiry 6 months from `source_date` unless renewed by independent eval.

- Parallel Search 98% SimpleQA at $0.005/req [vendor-reported, Parallel, 2026-05-25]
- Exa 91% SimpleQA / 24% FreshQA — semantic strength, freshness weakness [vendor-reported, Parallel citing Exa, 2026-05-25]
- Tavily 93% SimpleQA, ~1928 tok/result vs Parallel ~918 [vendor-reported, Parallel, 2026-05-25]
- Perplexity Sonar 92% SimpleQA, 50 req/min cap [vendor-reported, Parallel, 2026-05-25]
- Per-provider enrichment match rate 50–75% (waterfall motivation) [multi-source, 2026]

## Private-capital workflow examples

Each is `request → route plan`.

1. **"Enrich these 800 LPs with AUM band, recent commitments, IR contact"**
   `(enrich, open_web, medium, freshness_days=90, rows=800)` → Parallel Task (core) primary → Exa enrichment fallback → Brave for residual misses → verifier on rows with confidence < 0.70.

2. **"Find all GPs raising Fund III in EU climate, <€500M"**
   `(discover, open_web, medium, freshness_days=30)` → Parallel FindAll primary. No viable alternative in current vendor set.

3. **"Diligence Acme Capital — competitive position, team, churn signals"**
   `(research, open_web, high, freshness_days=30)` → Parallel Task (pro) primary with per-field citations → Perplexity Sonar synthesis leg → independent verifier on every claim with confidence < 0.80.

4. **"Pull SEC ADV-2 facts for these 50 RIAs"**
   `(extract, filings, high, rows=50)` → today: clear "needs Extract/EDGAR layer" caveat; tomorrow: Parallel Extract as schema enforcer.

5. **"Alert me when any portfolio company executive changes"**
   `(monitor, event_stream, medium)` → today: clear "event_stream needs Monitor API" caveat; tomorrow: Parallel Monitor.

6. **"Quick brief: state of placement-agent market in healthcare"**
   `(brief, open_web, low)` → Parallel Search (retrieve, dense excerpts) → Perplexity Sonar (synthesize). Two vendors on purpose — diversifies failure mode.

7. **"Look up the IR head at Sigma Partners"**
   `(enrich, open_web, low, rows=1)` → Brave or Parallel Search single shot. Waterfall + verifier off.

## What the current code already supports (as of PR2)

- 11-axis capability scores per provider in [provider_knowledge.py](../src/ct_search/provider_knowledge.py).
- 8-axis `_prompt_profile` (freshness, citations, deep-research, latency, cost, etc.) in [providers.py](../src/ct_search/providers.py).
- Five routing strategies (`single_provider`, `primary_with_fallback`, `primary_with_verification`, `retrieve_then_synthesize`, `waterfall`).
- `RouteStep` model with role (primary/fallback/verification/synthesis) — supports ordered plans.
- Manual provider override path — preserves operator control.
- **PR1 additions**: `job_type`, `source_shape`, `evidence_risk`, `freshness_days`, `scale_hint` on `ResearchRequest`; evidence-risk floor; source-shape gating; freshness penalty; waterfall emission at scale.
- **PR2 additions**: `CapabilityMetric` with `origin`/`source_url`/`source_date`/`expires_at`/`confidence` (5 providers seeded with vendor-reported provenance pointing at the original Parallel articles); `ProviderEconomics` (avg_tokens_per_result, avg_match_rate); `estimated_cost_per_grounded_row` on `RouteDecision` and every `RouteStep` (waterfall plans decay residual miss-rate so step 2's cost is weighted by `(1 − match_rate_1)` etc.); depth-aware Parallel processor escalation (`_processor_for_request` bumps tier on deep-research signals, high evidence_risk, or filings source_shape — not just on field count).

## What the current code is still missing

PR3 closed the calibration loop; PR4 closed the executor gap. The remaining gaps are deliberate product decisions, not framework holes:

- **`source_shape` / `freshness_days` / `scale_hint` are still backend-only.** They route correctly via the API and are surfaced on result chips after a run, but the workbench has no operator-facing control for them yet. This is the next UX win.
- **No SerpAPI-class, Parallel Extract, or Parallel Monitor providers wired.** Edge jobs (`serp_vertical`, `known_url`, `event_stream`) currently fail loudly with caveats per R2/architecture filter. Wire when the workflow justifies the integration cost.
- **Workbench does not yet POST to `/api/telemetry/outcome` automatically.** The endpoint and shape are live; the UI hook (accept/reject buttons + export pings) is the only missing front-end wire.
- **Recompute job runs on-demand only.** Schedule (`cron`, GH Actions, Prefect) is a deployment choice, not a code choice. Spec target cadence is weekly.
- **Eval set is 13 cases.** Spec target is 50–100; this is seed coverage and is expected to grow as operators flag misroutes.
- **Non-Parallel enrichment is heuristic.** `_targeted_search_enrichment` runs a real per-row search via Brave/Exa/Tavily/Perplexity and captures cited snippets into `source_notes` / `recent_signal`, but it can't fill arbitrary structured fields the way Parallel Task can. The merge step preserves primary values and only fills blanks, so the worst case is "fallback adds an independent citation"; the best case is "fallback fills a previously-empty field." This is honest until we add a small extraction step.

## Data model — request + plan

```python
# src/ct_search/models.py

JobType      = Literal["discover", "enrich", "research", "monitor", "extract", "brief", "verify"]
SourceShape  = Literal["open_web", "known_url", "similar_to", "serp_vertical",
                       "filings", "event_stream", "static_database"]
EvidenceRisk = Literal["low", "medium", "high"]

class ScaleHint(BaseModel):
    rows: int | None = None
    max_budget_usd: float | None = None

class ResearchRequest(BaseModel):
    # Existing (kept for back-compat)
    mode: ResearchMode = "search"
    query: str = Field(default="", max_length=4000)
    rows: list[dict] = Field(default_factory=list)
    fields: list[str] = Field(default_factory=list)
    routing_mode: RoutingMode = "best"
    provider: ProviderId | None = None
    max_results: int = Field(default=8, ge=1, le=25)
    # PR1 additions (all optional; inferred from existing fields when omitted)
    job_type: JobType | None = None
    source_shape: SourceShape = "open_web"
    evidence_risk: EvidenceRisk = "medium"
    freshness_days: int | None = None
    scale_hint: ScaleHint | None = None
```

`RoutePlan` is the existing `RouteDecision.steps: list[RouteStep]` extended so waterfall plans emit multiple `role="fallback"` steps in priority order.

## Calibration loop **[PR3]**

```python
class RouteTelemetry(BaseModel):
    route_plan_id: str
    request_shape: dict  # job_type, source_shape, evidence_risk, freshness_days, rows, fields_count
    step_results: list[dict]  # per-step: provider, role, latency_ms, cost_usd, returned_rows,
                              #          null_rate, citation_coverage, avg_confidence, low_confidence_rate
    user_outcome: dict | None  # accepted_rows, rejected_rows, exported, edited_fields
```

Cadence: daily ops metrics, weekly score adjustments from telemetry, monthly eval-set refresh and benchmark-metadata expiry review. Eval set: 50–100 Edna-native queries split across the six job types with private-capital examples (LP enrichment, fund-close signals, RIA ADV extraction, sector-focused GP discovery, diligence profiles, placement-agent landscape briefs).

## Open risks & falsification

- **Vendor-reported numbers may overstate real private-capital performance.** Falsified if Edna eval shows materially different ranking after 90 days of traffic.
- **`evidence_risk` may be misclassified from natural language.** For now it's an explicit operator control. Falsified if users frequently override route plans.
- **Waterfalls may raise cost without improving accepted rows.** Falsified if fallback rows have low acceptance/export rates.
- **Freshness penalties may underuse good semantic providers.** Falsified if Exa-like routes perform well on fresh Edna evals.
- **Static databases may matter more than expected for customer workflows.** Falsified if users repeatedly request baseline fund/contact fields rather than live-web augmentation.

## Phasing

- **PR1 ✓ shipped.** Request primitives + evidence-risk floor + source-shape gating + freshness penalty + waterfall emission. Existing API is fully back-compat: all new fields are optional. Workbench surfaces `evidence_risk` as a 3-button control.
- **PR2 ✓ shipped.** `CapabilityMetric` with provenance, expiry, origin, and confidence-in-the-score. `ProviderEconomics` (tokens/result, match-rate) per provider. Cost-per-grounded-row replaces per-request pricing as the headline cost (per call cost kept as a secondary metric). Processor-tier escalation by depth signal (deep-research signals + high evidence_risk + filings source_shape).
- **PR3 ✓ shipped.** Logfire-instrumented telemetry (`src/ct_search/telemetry.py`) emits one structured span per `route_plan` with full `request_shape`, `plan`, and `step_results`, and also appends a JSONL row to `output/telemetry.jsonl` (path overridable via `CT_SEARCH_TELEMETRY_PATH`). `POST /api/telemetry/outcome` attaches accept/reject/export signals. The recompute job in `src/ct_search/eval/recompute_scores.py` joins plans with outcomes, applies a Bayesian-flavored prior update at ≥5 samples, and writes `output/metric_overrides.json`. The eval harness in `src/ct_search/eval/run_eval.py` runs 13 Edna-native cases per CI and produces `output/eval_scoreboard.json`. The workbench renders `[origin · score · expires_at]` chips on each provider tile, with a `is-stale` badge when `expires_at` is past.

Pydantic-stack alignment: Logfire is the telemetry backbone (no separate datastore for PR3). FastAPI, httpx, and Pydantic validation events are auto-instrumented. When a `LOGFIRE_TOKEN` is set, spans flow to the Logfire UI; without one, structured data still lands in the JSONL sink that the recompute job consumes. No `pydantic_ai` is wired today — there is no agent loop yet; adding one (e.g. an "explain this route plan" agent) would be the natural place to introduce it via `logfire.instrument_pydantic_ai()`.

- **PR4 ✓ shipped.** Plan executor (`run_research` in [providers.py](../src/ct_search/providers.py)) walks `route.steps` in order. **Primary** runs on the full input. **Fallback** runs on rows the primary left blank (enrich mode) or supplements unique URLs (search mode). **Verifier** re-runs low-confidence rows through an independent provider; agreement bumps confidence and sets `verified=true`, disagreement attaches a dispute citation. **Synthesis** routes grounded excerpts through a second provider to produce a single brief. Non-Parallel enrichment is no longer demo-only — `_targeted_search_enrichment` builds a per-row query against any wired search provider and captures cited snippets. Per-row attribution (`provider`, `step_role`, `verified`, `contributing_providers`) flows through to the table, CSV, and PDF. Telemetry records one `StepResult` per executed step (not just primary).
