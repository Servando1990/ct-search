# AGENTS.md

Guidance for future coding agents working in this repository.

## Project Shape

Edna Search is a split application with two frontend surfaces in one Next app:

- `src/ct_search/`: Python FastAPI backend for provider routing, spreadsheet preview, research/enrichment runs, and CSV/PDF export.
- `frontend/`: Next.js 16, React 19, and TypeScript frontend.
  - `/`: public launch page for positioning, proof, and demo conversion.
  - `/workbench`: operator product surface for upload, routing, review, and export.
- `tests/`: FastAPI API tests.

Keep Python on the backend. TypeScript is expected and preferred in the frontend.

## Product Context

This is a commercial SaaS-style research and enrichment tool for placement agents and private-capital teams — "the OpenRouter for search agents, with the router as the core offering." Keep the public launch page and working product surface separate: `/` explains and converts, while `/workbench` is the routing desk. Full product spec and status: `docs/spec.md`.

Core workflow (zero-config by design):

1. Write a natural-language brief and/or attach a CSV/XLSX contact list. Nothing else is required.
2. The intent parser (`src/ct_search/intent.py`) fills the routing primitives from the brief; the router plans the run (primary → fallback → verifier → synthesis). Tuning (risk, venue, fields) is progressive disclosure, never a prerequisite. Operator-tuned values always beat inferred ones.
3. The run executes async with live step progress; the execution report explains the route after the run.
4. Review cited rows (keep/drop feeds calibration telemetry), export CSV or PDF.

Design context lives in `PRODUCT.md` (audience, voice, principles) and `DESIGN.md` (design system); follow both before making frontend changes.

## Backend Commands

```bash
uv sync --extra dev
uv run ruff check .
uv run pytest
./.venv/bin/uvicorn ct_search.main:app --host 127.0.0.1 --port 8000
```

Backend environment variables are documented in `.env.example`.

## Frontend Commands

```bash
cd frontend
npm install
npm run lint
npm run build
npm run dev
```

The frontend proxies backend calls through `/backend/*` using `frontend/next.config.ts`.

## Implementation Notes

- Provider/router logic belongs in `src/ct_search/providers.py`; routing behavior must stay consistent with `docs/decision-framework.md` — when they disagree, fix the code or update the doc, never silently diverge.
- Intent parsing (brief → routing primitives) belongs in `src/ct_search/intent.py`.
- Async run management belongs in `src/ct_search/runs.py`; persistence in `src/ct_search/store.py` (SQLite at `output/edna.db`).
- API models belong in `src/ct_search/models.py`.
- Frontend API calls belong in `frontend/src/lib/api.ts`.
- Launch page behavior belongs in `frontend/src/app/page.tsx`.
- Main React workspace behavior belongs in `frontend/src/components/Workspace.tsx`, mounted from `frontend/src/app/workbench/page.tsx`.
- Use `lucide-react` icons for UI controls.
- Do not commit generated folders such as `.venv/`, `.pytest_cache/`, `.ruff_cache/`, `frontend/node_modules/`, `frontend/.next/`, `.playwright-cli/`, or `output/`.

## Quality Bar

Before handing off meaningful changes, run the relevant checks:

```bash
uv run ruff check .
uv run pytest
uv run python -m ct_search.eval.run_eval   # 51 routing cases must stay green
cd frontend && npm run lint && npm run build
```

For frontend changes, also do a browser smoke test against:

- Backend: `http://127.0.0.1:8000`
- Launch page: `http://127.0.0.1:3000`
- Workbench: `http://127.0.0.1:3000/workbench`

Check upload/search, provider routing, demo research results, and export buttons when possible.
