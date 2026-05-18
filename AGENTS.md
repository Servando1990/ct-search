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

This is a commercial SaaS-style research and enrichment tool for placement agents and private-capital teams. Keep the public launch page and working product surface separate: `/` explains and converts, while `/workbench` stays the dense operator workflow.

Core workflow:

1. Upload a CSV/XLSX contact list or enter a natural-language research brief.
2. Choose a provider route by best fit, cost, speed, confidence, or manual provider.
3. Review cited results with confidence and provider attribution.
4. Export CSV or PDF.

Design context lives in `.impeccable.md`; follow it before making frontend changes.

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

- Provider logic belongs in `src/ct_search/providers.py`.
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
cd frontend && npm run lint && npm run build
```

For frontend changes, also do a browser smoke test against:

- Backend: `http://127.0.0.1:8000`
- Launch page: `http://127.0.0.1:3000`
- Workbench: `http://127.0.0.1:3000/workbench`

Check upload/search, provider routing, demo research results, and export buttons when possible.
