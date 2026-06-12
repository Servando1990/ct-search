# Edna Search — Overview

A plain-language companion to [decision-framework.md](decision-framework.md). The decision
framework is the canonical routing spec; this document is the orientation map: what Edna is,
why it exists, how a user moves through it, and how the pieces fit together.

## Summary

Edna Search is a **live-web research and enrichment workbench for capital-formation teams**
(placement agents, LP mappers, diligence/IC teams). It is deliberately *not* a
Preqin/PitchBook/EDGAR/CRM replacement. It is the **augmentation layer** that fills the columns
those systems can't: recent fundraising signals, hiring velocity, sector positioning,
IR-contact freshness, and export-ready *cited* evidence.

The core innovation is the **router**: it does not "pick the best vendor." It picks the
**research route** that matches the job's *failure cost*, conditioned on three axes — `job_type`
(what you're doing), `source_shape` (where the answer lives), and `evidence_risk` (what happens
if we're wrong). Vendor capability is a *prior with provenance and expiry*, continuously
calibrated by Edna's own telemetry — never hardcoded routing law.

Implementation shipped in four backend PRs, then the phase 1–3 roadmap on top
(full ledger with commits: [spec.md](spec.md) §3):

| Stage | What landed |
|---|---|
| **PR1** | Request primitives (`job_type`, `source_shape`, `evidence_risk`, `freshness_days`, `scale_hint`), evidence-risk floor, source-shape gating, freshness penalty, waterfall emission |
| **PR2** | `CapabilityMetric` with vendor-reported provenance + expiry; `ProviderEconomics`; **cost-per-grounded-row** (not per-request price); depth-aware Parallel processor escalation |
| **PR3** | Logfire telemetry (one span per route plan) + JSONL sink, `/api/telemetry/outcome` hook, eval harness, score-recompute job, provenance chips in UI |
| **PR4** | **Plan executor made real** — walks every step (primary → fallback → verifier → synthesis), per-row `via {provider} · {step_role}` attribution, `verified` flag on independent agreement, flows through table + CSV/PDF |
| **Phase 1** | **The routing desk**: prompt-first workbench (one composer, tuning behind progressive disclosure, execution report after the run), LLM intent parser filling the request primitives from the brief, outcome telemetry wired from keep/drop + export |
| **Phase 2** | **Async runs**: SSE progress streaming with live step rail, SQLite persistence + run history, per-run budget caps, live enrichment on by default behind the cap |
| **Phase 3** | **Data-backed router**: calibration posteriors applied at routing time, keyless EDGAR filings venue, known_url extraction route (Tavily Extract / Exa contents), Perplexity deep-research escalation, 51-case eval (which caught and fixed a missing high-risk verifier on synthesis routes) |
| **Phase 4 (specced)** | Search & match — identity resolution + thesis/deal-investor matching ([match-spec.md](match-spec.md)) |

## Value proposition

```
╭──────────────────────────────────────────────────────────────────╮
│  "Turn raw lists into cited rows you can defend."                  │
╰──────────────────────────────────────────────────────────────────╯

  For:      placement agents & capital-formation teams under time pressure
  Who:      hold valuable but incomplete contact lists in spreadsheets
  Edna is:  a research + enrichment workbench
  That:     routes each job to the right provider plan and returns
            confidence-scored, source-attributed, exportable rows
  Unlike:   single-vendor search wrappers or static databases
  It:       conditions routing on failure cost, keeps provider choice +
            cost + citations + audit trail visible, and calibrates its
            own vendor priors from real outcomes
```

Three differentiators that competitors don't combine:

- **Route by failure cost, not vendor brand.** Missing a target in sourcing is recoverable;
  citing an unverifiable fact in an IC memo is not — the router enforces that asymmetry
  (`evidence_risk` floor + mandatory verifier at `high`).
- **Cost honesty.** Headline cost is `cost_per_grounded_row` (search + extraction + downstream
  tokens + miss-rate-weighted fallbacks + verifier), not the misleading per-request price.
- **Self-calibrating priors.** Every vendor number is labeled `[origin · score · expires_at]`
  and decays unless re-validated by Edna's own telemetry.

## User journey

```
 LANDING (/)                                WORKBENCH — the routing desk
╭───────────────────────╮                 ╭───────────────────────────────────╮
│ 1. See the claim +     │   "Open         │ 2. BRIEF: one composer — write the │
│    product proof frame │    workbench"   │    brief and/or attach a CSV/XLSX. │
│    (brief→route→cost→  │ ───────────────▶│    Nothing else required; tuning   │
│    confidence→rows)    │                 │    (risk/venue/fields) is optional │
╰───────────────────────╯                 ╰─────────────────┬─────────────────╯
                                                            │
                                                            ▼
                          ╭─────────────────────────────────────────────────╮
                          │ 3. INTENT: Edna reads the brief → job_type,      │
                          │    source_shape, evidence_risk, freshness,      │
                          │    fields (LLM; keyword fallback without a key) │
                          ╰─────────────────┬───────────────────────────────╯
                                            ▼
                          ╭─────────────────────────────────────────────────╮
                          │ 4. RUN (async) → live step rail as the executor │
                          │    walks primary → fallback → verifier →        │
                          │    synthesis, under the per-run budget cap      │
                          ╰─────────────────┬───────────────────────────────╯
                                            ▼
                          ╭─────────────────────────────────────────────────╮
                          │ 5. REVIEW: execution report (strategy, steps,   │
                          │    venue scores, caveats, signal provenance) +  │
                          │    cited rows with per-row attribution          │
                          ╰─────────────────┬───────────────────────────────╯
                                            ▼
                          ╭─────────────────────────────────────────────────╮
                          │ 6. KEEP/DROP + EXPORT CSV/PDF → outcomes feed   │
                          │    the calibration loop; runs persist and       │
                          │    reopen from Recent runs                      │
                          ╰─────────────────────────────────────────────────╯
```

## System architecture

```
╭───────────────────────── FRONTEND (Next.js 16 / React 19) ─────────────────────────╮
│  /  page.tsx (launch)            /workbench  Workspace.tsx (operator UI)            │
│  shared design system: green-tinted OKLCH neutrals, forest accent, mono for figures │
╰───────────────────────────────────────────┬────────────────────────────────────────╯
                                  /backend/* proxy (next.config.ts)
                                             ▼
╭──────────────────────────── BACKEND (FastAPI · src/ct_search) ─────────────────────╮
│                                                                                     │
│  main.py ── /api/runs (async + SSE) · /api/research (sync) ────────┐                │
│      │                                                             │                │
│      ▼                                                             ▼                │
│  runs.py (asyncio task per run, event fanout) ──▶ store.py (SQLite: runs + events)  │
│      │                                                                              │
│      ▼                                                                              │
│  intent.py — brief → job_type · source_shape · evidence_risk · freshness · fields   │
│  (Claude structured outputs; keyword heuristics without a key; operator wins)       │
│      │                                                                              │
│      ▼                                                                              │
│  providers.py  choose_provider() + run_research() executor                          │
│        │                 │                          │             │                 │
│        ▼                 ▼                          ▼             ▼                 │
│  ROUTER (R1–R8,     provider_knowledge.py     PLAN EXECUTOR   telemetry.py          │
│  task-conditional   capability priors +       primary→        log_route_plan()      │
│  capability mask)   provenance/expiry +       fallback→       ├─ Logfire span       │
│   job_type ×        calibration OVERRIDES     verifier→       └─ output/telemetry   │
│   source_shape ×    from metric_overrides     synthesis            .jsonl           │
│   evidence_risk     .json (posteriors)        (budget-capped)                       │
│        │                                                              │             │
│        ▼                                                              ▼             │
│  RoutePlan (ordered steps + cost_per_grounded_row)        /api/telemetry/outcome    │
│                                                                       │             │
│  venues: Parallel · Exa · Tavily · Brave · Perplexity ·               ▼             │
│  EDGAR (keyless, filings) — demo mode for unkeyed venues  eval/recompute_scores.py  │
│  extraction: Tavily Extract / Exa contents (known_url)   → metric_overrides.json    │
│  exports: CSV · PDF                                    eval/run_eval.py (51 cases)  │
╰─────────────────────────────────────────────────────────────────────────────────────╯
                          ▲                                         │
                          └──────── recompute updates venue ────────┘
                                    priors from real outcomes
```

## Routing decision flow (the heart of it)

```
ResearchRequest (job_type, source_shape, evidence_risk, freshness_days, scale_hint)
        │
        ▼
(1) EVIDENCE-RISK FLOOR    high → citations + verifier required
                           medium → citations required · low → desk scan
        │
        ▼
(2) ARCHITECTURE FILTER    (job_type × source_shape) → eligible provider classes
                           out-of-scope shape → FAIL LOUDLY with caveat (R1, R2)
        │
        ▼
(3) FRESHNESS PENALTY      score *= clamp(1 − age/freshness_days, 0.2, 1.0)
    (rank, not exclude)    override: similar_to suspends penalty (R3, F2)
        │
        ▼
(4) RANK                   task-conditional capability mask × constraint
                           (cost / speed / coverage / quality)
        │
        ▼
(5) EMIT RoutePlan         cheap_scan → targeted_enrich → verify_subset → synthesize
                           waterfall fallbacks when rows ≥ 50 (R6)
        │
        ▼
(6) LOG telemetry          one span/plan → calibration loop  [PR3]
```

## Key takeaways

- **The router is the product.** Five strategies (`single_provider`, `primary_with_fallback`,
  `primary_with_verification`, `retrieve_then_synthesize`, `waterfall`) are emitted from
  testable, ordered rules (R1–R8, F1–F2, C1) tied to a written spec the code must not silently
  diverge from.
- **Honesty is designed in.** Scope honesty (not a database replacement), cost honesty
  (per-grounded-row), and provenance honesty (vendor numbers labeled + expiring) are
  first-class, not marketing.
- **The loop is closed.** Plans are executed for real (PR4), outcomes are captured (PR3), and
  priors recompute from those outcomes — so the system improves with use rather than ossifying
  around vendor claims.
- **Two surfaces, one system.** Landing converts (positioning + product proof); workbench
  operates (dense, table-first). Both share one green-tinted OKLCH design system with no
  SaaS-cream, no nested cards, no AI-template scaffolding.
- **Deliberate gaps remain, by choice.** SerpAPI verticals and Monitor/event-stream providers
  aren't wired (fail loudly with caveats); non-Parallel enrichment is citation-capturing, not
  arbitrary-field extraction; FindAll-class discovery is flagged but not wired. Closed since
  this doc was first written: outcome telemetry flows from the workbench and recomputed
  posteriors now move the router's priors; the LLM intent parser fills the routing primitives
  from the brief; filings route to a keyless EDGAR provider; known-URL briefs run real
  extraction (Tavily Extract / Exa contents); the eval set is 51 routing cases.

## Where to go next in the codebase

| Concern | File |
|---|---|
| Product spec + status ledger | [docs/spec.md](spec.md) |
| Routing spec (canonical) | [docs/decision-framework.md](decision-framework.md) |
| Search & match (Phase 4 spec) | [docs/match-spec.md](match-spec.md) |
| Router + plan executor | [src/ct_search/providers.py](../src/ct_search/providers.py) |
| Intent parsing (brief → primitives) | [src/ct_search/intent.py](../src/ct_search/intent.py) |
| Async runs + persistence | [src/ct_search/runs.py](../src/ct_search/runs.py), [src/ct_search/store.py](../src/ct_search/store.py) |
| Provider priors + provenance + overrides | [src/ct_search/provider_knowledge.py](../src/ct_search/provider_knowledge.py) |
| API models | [src/ct_search/models.py](../src/ct_search/models.py) |
| Telemetry + calibration | [src/ct_search/telemetry.py](../src/ct_search/telemetry.py), [src/ct_search/eval/](../src/ct_search/eval/) |
| Landing page | [frontend/src/app/page.tsx](../frontend/src/app/page.tsx) |
| Workbench (the routing desk) | [frontend/src/components/Workspace.tsx](../frontend/src/components/Workspace.tsx) |
| Design system | [DESIGN.md](../DESIGN.md), [frontend/src/app/desk.css](../frontend/src/app/desk.css), [frontend/src/app/globals.css](../frontend/src/app/globals.css) |
