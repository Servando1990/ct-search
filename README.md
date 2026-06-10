# Edna Search

A Python-first research and enrichment workbench for placement agents and private-capital teams.

The app lets users upload a CSV/XLSX contact list or describe a natural-language search, route the job to the best search provider by cost, speed, or confidence, then export the result as CSV or PDF.

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

Without API keys, the app runs in demo mode so the workflow is still testable. Add keys from `.env.example` to enable live providers.

## Provider Routing

The provider layer is inspired by `pi-websearch`: a small normalized interface, a provider registry, and a router that can auto-select from available credentials. Edna Search extends that idea for a SaaS product by scoring providers on estimated cost, speed, coverage, and confidence.

The router's inputs are zero-config: an LLM intent parser (`src/ct_search/intent.py`, Claude structured outputs) reads the brief and fills `job_type`, `source_shape`, `evidence_risk`, `freshness_days`, and the returned fields. Operator-tuned values always win, and without `ANTHROPIC_API_KEY` the router falls back to keyword heuristics so demo mode needs no key.

Implemented adapters:

- Parallel Search API through the Python SDK with REST fallback.
- Brave Search API.
- Exa Search API.
- Tavily Search API.
- Perplexity Sonar.

Bulk enrichment defaults to demo mode unless `CT_SEARCH_LIVE_ENRICHMENT=1`, because live per-row Task API enrichment can be slow and costly. The code path is present for small batches.
