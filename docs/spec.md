# Edna Search — Product Spec & Implementation Status

> Last updated: 2026-06-12. Companion docs: [overview.md](overview.md) (orientation map),
> [decision-framework.md](decision-framework.md) (canonical routing spec),
> [../PRODUCT.md](../PRODUCT.md) (brand/product register), [../DESIGN.md](../DESIGN.md)
> (design system).

## 1. Vision

**Edna is the OpenRouter for search agents, with the router as the core offering.**

The user configures nothing. They write a plain-English brief (or attach a contact
list), and Edna decides everything: which search vendor to use, for which parts of
the job, and in what shape — pure search, search + extraction, deep research, or
primary-source filings lookup. The router knows the optimal route and brings back a
clean, cited, exportable result.

The capabilities are industry-agnostic, but the product is focused on **private
capital** first: PE funds, VCs, independent sponsors, family offices, angels,
placement agents, and broker-dealers. Search & match is a core demand in due
diligence and capital formation across that entire vertical.

The product metaphor is **smart order routing** — vocabulary this audience already
owns. The brief is the ticket, venues are the search providers, the run comes back
as an execution report (strategy, steps, costs, fills) plus a reviewable ledger of
cited rows.

### Three differentiators

1. **Route by failure cost, not vendor brand.** Missing a lead in sourcing is
   recoverable; an unverifiable citation in an IC memo is not. High evidence risk
   forces citations plus an independent verifier step — on every strategy,
   including synthesis routes.
2. **Cost honesty.** The headline number is cost-per-grounded-row (search +
   extraction + downstream tokens + miss-rate-weighted fallbacks + verifier), not
   the misleading per-request price.
3. **Self-calibrating priors.** Every vendor capability number carries origin,
   confidence, and expiry — and is recomputed from Edna's own run outcomes, so the
   router improves with use instead of ossifying around vendor claims.

## 2. Architecture

```
Next.js 16 frontend (frontend/)
  /            launch page — positioning + product proof
  /workbench   the routing desk — composer, live run progress, ledger, history
        │  /backend/* proxy
        ▼
FastAPI backend (src/ct_search/)
  main.py       API: /api/research (sync), /api/runs (+SSE events), preview, exports, telemetry
  intent.py     LLM intent parser (Claude structured outputs) + heuristic fallback
  providers.py  router (rules R1–R8), plan executor, provider adapters
  provider_knowledge.py  capability priors + provenance + calibration overrides
  runs.py       async run manager (asyncio tasks, SSE fanout)
  store.py      SQLite persistence (runs + progress events) at output/edna.db
  telemetry.py  route-plan + outcome logging (Logfire + JSONL)
  eval/         51-case routing eval + nightly score recompute
```

### The request lifecycle

1. **Brief in.** The workbench sends only what the operator explicitly tuned;
   everything else is left for inference.
2. **Intent resolution** (`intent.py`). Claude (model `CT_SEARCH_INTENT_MODEL`,
   default `claude-opus-4-8`) maps the brief to the five routing primitives:
   `job_type`, `source_shape`, `evidence_risk`, `freshness_days`, and the returned
   field schema. Operator-set values always win. Without `ANTHROPIC_API_KEY`,
   deterministic keyword heuristics fill the gaps (URLs → `known_url`, filings
   vocabulary → `filings`, etc.). Provenance (`operator` / `llm` / `heuristic`) is
   recorded and shown in the UI.
3. **Routing** (`choose_provider`). Ordered, testable rules:
   - R1 evidence-risk floor — medium risk requires citation capability ≥ 0.70,
     high risk ≥ 0.85 plus a mandatory independent verifier step.
   - R2 architecture filter — specialist venues only compete inside their shape
     (EDGAR never contends for open-web jobs); unsupported shapes
     (SERP verticals, event streams, static databases) fail loudly with caveats.
   - R3/F2 similar_to → semantic venues, freshness penalty suspended.
   - F1 freshness penalty — tight windows demote stale-index venues (rank, not
     exclude).
   - R6 waterfall — enrichment at ≥ 50 rows emits fallback steps to recover
     null fields.
   - R8 brief jobs retrieve-then-synthesize (with verifier at high risk).
   - Vendor capability scores are task-conditional and come from
     `provider_knowledge()` — priors overridden by recomputed posteriors.
4. **Execution** (`run_research`). Walks the plan: primary → fallback (re-runs
   only missed rows) → verifier (re-checks low-confidence rows, marks `verified`
   on independent agreement) → synthesis. Emits progress events; respects the
   per-run budget cap.
5. **Review + export.** Per-row attribution (`via {venue} · {step role} ·
   ✓ verified`), citations, confidence. Keep/drop toggles and exports POST
   outcomes to `/api/telemetry/outcome`.
6. **Calibration.** `eval/recompute_scores.py` joins route plans with outcomes and
   writes posterior capability scores to `output/metric_overrides.json`;
   `provider_knowledge()` applies them at lookup time with `usage_telemetry`
   provenance.

### Venues (providers)

| Venue | Key | Capabilities wired |
|---|---|---|
| Parallel | `PARALLEL_API_KEY` | search, Task API enrichment (processor escalation lite→pro) |
| Brave | `BRAVE_API_KEY` | fast fresh search |
| Exa | `EXA_API_KEY` | semantic search, `/contents` extraction |
| Tavily | `TAVILY_API_KEY` | search, Extract endpoint |
| Perplexity | `PERPLEXITY_API_KEY` | answer synthesis (sonar; sonar-pro for deep research) |
| **EDGAR** | **none — keyless** | SEC full-text search for filings shapes (Form D, 13F, 8-K, 10-K, S-1…) |

Any venue without a key runs in demo mode with honest warnings. EDGAR is live out
of the box.

## 3. Implementation status (by phase)

### Phase 1 — Prompt-first UX + LLM intent parser ✅
Commits `12bacde`, `63abadb`.
- Workbench inverted to prompt-first: one composer (brief + CSV/XLSX attach),
  zero pre-run config, tuning behind a single progressive disclosure
  (optimize-for, evidence risk, venue override, returned fields — all default
  "auto").
- Post-run **execution report**: strategy, plain-English reason, framework
  signals with provenance, step rail with per-step call and grounded-row costs,
  venue-scores disclosure, caveats.
- LLM intent parser with operator-wins override semantics and no-key heuristic
  fallback.
- Outcome telemetry wired from the UI (keep/drop, export) — closes the
  calibration loop's input side.
- Landing page repositioned: "Smart order routing, for research."

### Phase 2 — Async execution + persistence ✅
Commits `c4eacde`, `b0aab4c`.
- `POST /api/runs` schedules the executor as a background task;
  `GET /api/runs/{id}/events` streams progress (SSE with persisted replay and
  sequence dedupe; polling fallback in the UI).
- The routing stage shows the planned step rail filling in live
  (pending → running → done/error).
- Runs persist to SQLite (`output/edna.db`); recent runs list on the composer;
  past runs reopen with full report + ledger; orphaned runs recovered on
  restart.
- **Budget caps**: `CT_SEARCH_MAX_RUN_BUDGET_USD` (default $2.00) skips
  post-primary live steps past the cap (demo steps stay free) and demotes live
  enrichment to demo when its estimate exceeds the budget.
- **Live enrichment defaults ON** behind the cap (was: off behind a 5-row gate).
  `CT_SEARCH_LIVE_ENRICHMENT=0` is the kill switch.

### Phase 3 — Data-backed router ✅
Commits `c048a76`, `2209b95`, `54b8a52`, `7ddeaf9`, `64d4035`.
- **Calibration loop closed**: recomputed posteriors in
  `output/metric_overrides.json` replace vendor priors at routing time, with
  sample counts surfaced as provenance chips.
- **EDGAR filings venue** (keyless): filings-shaped briefs return real SEC
  filings with sec.gov citation URLs at $0. Includes transient-500 retries,
  brief→FTS query cleaning with a relaxation ladder, form filters (D, 13F,
  8-K, 10-K, 10-Q, S-1, SC 13D), freshness → date-range mapping, and the R2
  specialist gate.
- **Extraction route**: URL-bearing briefs route `known_url` to Tavily Extract /
  Exa contents and return cited page-content rows.
- **Deep-research escalation**: Perplexity sonar → sonar-pro on deep-research
  profiles.
- **Eval set 13 → 51 routing cases** across the framework surface. The expansion
  caught and fixed a real rule violation (synthesis routes skipped the
  high-risk verifier).

### Phase 4 — Search & match (thesis matching) ✅
Commits `415a546`, `64aa252`, `87de8b9`, `ec49633`, `a8284e8`, `69b8ab5`,
`19a5a3b` (merged in #2). Full spec: [match-spec.md](match-spec.md).

Two layered meanings of "match": **identity match** (entity resolution, record
linkage, dedupe — the infrastructure; row merging now keys on a resolved
canonical identity, not naive string equality) and **thesis match** (the
product: scoring candidates against a business thesis — typically a deal or
transaction — with per-criterion cited evidence, disqualifiers, and a ranked,
defensible outreach list). The operator's "100 names with Y characteristics" is
always an instrument for a transaction looking for its counterparty;
deal-investor matching is the flagship flow.

- **4a — Resolution & linkage**: `resolve.py` (domain + CIK anchors, name
  normalization), `entities` table, executor merging swapped to `link()`,
  `match_basis` surfaced in the ledger.
- **4b — Dedupe on upload**: `dedupe()` clustering, `POST /api/dedupe` +
  `/api/dedupe/decision`, decisions recorded to telemetry; upload-preview
  dedupe banner with per-cluster merge / keep-separate.
- **4c — Thesis object + fit scoring**: `Thesis` extraction (`thesis.py`),
  evidence-per-criterion gathering, LLM judge with citation discipline, ranked
  ledger with fit bands + disqualifiers (live judge needs `ANTHROPIC_API_KEY`;
  without it criteria read `unknown`).
- **4d — Fit feedback loop**: `match_feedback` on `UserOutcome`,
  fit-calibration + linkage passes in `recompute_scores.py`.
- New `match` job type routes to a per-candidate `match_pipeline`
  (resolve → evidence → judge → verify), covered by eval routing cases.
- Still open: judge calibration against human-labeled criterion verdicts once
  live-judge traces accumulate (validate-evaluator methodology).

### Phase 5 — SaaS shell (NOT started)
Auth/accounts, usage metering and billing, CRM-friendly export targets.

### Known gaps, deliberately caveated in-product
- Monitor/event-stream providers not wired (fail loudly).
- SERP verticals (Scholar/Patents/Maps) not wired (fail loudly).
- FindAll-class discovery flagged but not wired; discovery runs on search venues.
- Form ADV is on IAPD (adviserinfo.sec.gov), not in EDGAR full-text search —
  documented as an EDGAR tradeoff.
- Eval asserts routing decisions; graded *result quality* needs live keys and
  human grading (next calibration milestone).
- Runs execute in-process (no external queue); a restart errors in-flight runs.

## 4. Configuration

Copy `.env.example` → `.env`. Everything is optional:

| Variable | Default | Effect |
|---|---|---|
| `PARALLEL_API_KEY` … `PERPLEXITY_API_KEY` | unset | enable live venues (unset → demo rows) |
| `ANTHROPIC_API_KEY` | unset | enable LLM intent parsing (unset → keyword heuristics) |
| `CT_SEARCH_INTENT_MODEL` | `claude-opus-4-8` | intent parser model |
| `CT_SEARCH_MAX_RUN_BUDGET_USD` | `2.00` | per-run live spend ceiling |
| `CT_SEARCH_LIVE_ENRICHMENT` | `1` | `0` forces demo enrichment regardless of keys |
| `CT_SEARCH_EDGAR_USER_AGENT` | set | SEC fair-access identification header |
| `CT_SEARCH_DB_PATH` | `output/edna.db` | runs/events SQLite location |
| `LOGFIRE_TOKEN` | unset | ship telemetry spans to Logfire |

## 5. How to run and test

```bash
# Backend
uv sync --extra dev
./.venv/bin/uvicorn ct_search.main:app --host 127.0.0.1 --port 8000

# Frontend (second terminal)
cd frontend && npm install && npm run dev
# → http://127.0.0.1:3000/workbench
```

**Quality gates** (all green as of this commit):

```bash
uv run ruff check .                          # lint
uv run pytest                                # 81 tests
uv run python -m ct_search.eval.run_eval     # 54/54 routing cases
cd frontend && npm run lint && npm run build
```

**Zero-config walkthroughs** (no keys needed):

1. *Filings, live:* type `Form D filings from healthcare fund sponsors` →
   routes `filings → EDGAR`, returns real SEC filings, LIVE at $0. Open a
   source link; export CSV.
2. *Demo route plan:* type `Map LPs that backed lower-middle-market healthcare
   funds since 2024` → watch the execution report, open "Venue scores — why
   this route", drop a row, export — then check
   `output/telemetry.jsonl` for the `user_outcome` row.
3. *History:* click "New run", see Recent runs, click an old run to reopen it.
4. *Tuning:* open "Tune route", set evidence risk high → the plan gains an
   independent verification step.

**Calibration loop:** after some runs with keep/drop activity,
`uv run python -m ct_search.eval.recompute_scores` writes posteriors; the next
route plan's venue-score chips show `usage_telemetry` provenance.
