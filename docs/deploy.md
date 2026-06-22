# Deploying Edna Search — Vercel + Modal

Two hosts, because each fits half the app:

- **Frontend (Next.js)** → **Vercel** (free). Native Next.js host; serves the UI
  and proxies `/backend/*` to the backend.
- **Backend (FastAPI)** → **Modal**. A stateful Python service — long-lived
  process, SSE streaming, SQLite + telemetry on a persistent Volume — which
  Modal runs as an always-warm container.

```
browser ──▶ Vercel (UI + /backend proxy) ──▶ Modal (*.modal.run) ──▶ SQLite on Volume
```

Everything runs in **demo mode at $0** until you add provider keys: EDGAR is
live and keyless, every other venue returns demo rows with honest warnings, and
the intent parser falls back to keyword heuristics without `ANTHROPIC_API_KEY`.

---

## 1. Backend → Modal

Prereqs: `pip install modal` and `modal token new` (once).

**a. (Optional) create the config secret.** Needed to set the frontend origin
for direct browser calls and to enable live providers. Demo mode works without
it.

```bash
modal secret create edna-search-secrets \
    CT_SEARCH_ALLOWED_ORIGINS=https://YOUR-FRONTEND.vercel.app
# add ANTHROPIC_API_KEY=... PARALLEL_API_KEY=... etc. to go live
```

**b. Deploy.**

```bash
modal deploy modal_app.py
```

Modal prints a public URL like
`https://<workspace>--edna-search-web.modal.run`. Copy it — that's the backend
URL for Vercel. Sanity check:

```bash
curl https://<workspace>--edna-search-web.modal.run/      # {"name":"Edna Search API","status":"ready"}
```

What [`modal_app.py`](../modal_app.py) sets up:

- Image built from `pyproject.toml` + `src/` (the `ct_search` package).
- A `modal.Volume` (`edna-data`) mounted at `/data`; `CT_SEARCH_DB_PATH` and
  `CT_SEARCH_TELEMETRY_PATH` point there, so run history + telemetry persist.
- `min_containers=1` → always warm (no cold starts; the in-memory run manager
  and SSE stream stay consistent). Drop to `0` to scale to zero — cheaper, but
  cold starts return. Persisted state survives either way via the Volume.

## 2. Frontend → Vercel

1. Import the repo at [vercel.com/new](https://vercel.com/new). Set **Root
   Directory = `frontend`** (Next.js is auto-detected).
2. Add an environment variable:
   `CT_SEARCH_BACKEND_URL = https://<workspace>--edna-search-web.modal.run`
3. Deploy. The `/backend/*` rewrite in
   [`next.config.ts`](../frontend/next.config.ts) now forwards to Modal.

Because the browser talks to `/backend` on the **Vercel** origin (same-origin)
and Vercel proxies server-side to Modal, no CORS is involved on the happy path —
the `CT_SEARCH_ALLOWED_ORIGINS` secret is belt-and-suspenders for any direct
browser calls.

## 3. Your domain

In the Vercel project → **Domains**, add a subdomain such as
`search.yoursite.com` and point your DNS `CNAME` at Vercel. That's the
"put it on my website" step. If you set a custom domain, add it to
`CT_SEARCH_ALLOWED_ORIGINS` too.

## 4. Verify

- Open the Vercel URL → `/workbench`.
- Run an example brief; the live step-rail should fill in (SSE; the UI falls
  back to polling automatically if a proxy buffers the stream).
- Reopen a past run — confirms the Volume-backed SQLite is persisting.

## Notes & limits

- **Demo vs live:** add provider keys to the Modal secret and redeploy to enable
  live venues. The `CT_SEARCH_MAX_RUN_BUDGET_USD` cap (default $2/run) guards
  spend the moment keys are present.
- **SSE through the Vercel proxy** can be buffered by the proxy layer on long
  runs; the workbench's polling fallback covers this. For pristine streaming you
  can instead point the browser directly at Modal (requires wiring the frontend
  `API_BASE` to the Modal URL and relying on `CT_SEARCH_ALLOWED_ORIGINS` for
  CORS) — not needed for a preview.
- **SQLite on a Volume** is fine for single-container preview traffic. For real
  concurrency, move to a hosted Postgres and the multi-container Modal pattern
  (`modal.Dict`/`Queue` for run state).
