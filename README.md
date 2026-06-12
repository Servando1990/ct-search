# Edna Search

Smart order routing for private-capital research — a Python-first research and enrichment desk for placement agents and capital-formation teams. Full spec: [docs/spec.md](docs/spec.md).

Zero-config by design: write a plain-English brief (and/or attach a CSV/XLSX contact list) and the router decides everything — which search venue, in which shape (search, extraction, deep research, SEC filings), with what evidence standard — then returns cited, confidence-scored rows you can review and export as CSV or PDF. Tuning controls exist but are never required.

## Architecture

```text
FastAPI backend (Python)
  - spreadsheet preview and parsing
  - provider registry and routing engine
  - live provider adapters
  - CSV/PDF exports

Next.js frontend (TypeScript)
  - public launch page at /
  - operator workbench
  - upload/search controls
  - provider scoring and manual selection
  - cited results table and exports
```

Python stays on the backend where provider orchestration, files, and exports belong. The frontend uses Next.js, React, and TypeScript because the product surface is highly stateful: uploaded rows, selected enrichment fields, routing preferences, async runs, warnings, citations, and exports.

## Quick Start

```bash
uv sync
./.venv/bin/uvicorn ct_search.main:app --host 127.0.0.1 --port 8000
```

In another terminal:

```bash
cd frontend
npm install
npm run dev
```

Then open [http://127.0.0.1:3000](http://127.0.0.1:3000) for the launch page or [http://127.0.0.1:3000/workbench](http://127.0.0.1:3000/workbench) for the product workbench.

Without API keys the web venues run in demo mode so the workflow is still testable — and **filings briefs are live anyway**: the EDGAR venue is keyless, so a brief like "Form D filings from healthcare fund sponsors" returns real SEC filings with sec.gov citations at $0. Add keys from `.env.example` to take the other venues (and the LLM intent parser) live.

## Provider Routing

The provider layer is inspired by `pi-websearch`: a small normalized interface, a provider registry, and a router that can auto-select from available credentials. Edna Search extends that idea for a SaaS product by scoring providers on estimated cost, speed, coverage, and confidence.

The router's inputs are zero-config: an LLM intent parser (`src/ct_search/intent.py`, Claude structured outputs) reads the brief and fills `job_type`, `source_shape`, `evidence_risk`, `freshness_days`, and the returned fields. Operator-tuned values always win, and without `ANTHROPIC_API_KEY` the router falls back to keyword heuristics so demo mode needs no key.

Implemented adapters:

- Parallel Search API through the Python SDK with REST fallback.
- Brave Search API.
- Exa Search API (search + /contents extraction).
- Tavily Search API (search + Extract).
- Perplexity Sonar (escalates to sonar-pro for deep research).
- SEC EDGAR full-text search — keyless, live out of the box for filings-shaped jobs.

## Async Runs

The workbench executes research as background runs: `POST /api/runs` schedules the plan, `GET /api/runs/{id}/events` streams progress (SSE — intent, planned route, each step starting/finishing), and `GET /api/runs` lists history. Runs and their events persist to SQLite at `output/edna.db` (`CT_SEARCH_DB_PATH` to override), so results survive reloads and past runs reopen from the desk. `POST /api/research` remains as the synchronous path for scripts.

Live per-row Task API enrichment is on by default, guarded by a per-run budget cap (`CT_SEARCH_MAX_RUN_BUDGET_USD`, default $2.00): live steps beyond the primary are skipped past the cap, and enrichment falls back to demo rows when its estimate exceeds the budget. `CT_SEARCH_LIVE_ENRICHMENT=0` forces demo enrichment regardless.
