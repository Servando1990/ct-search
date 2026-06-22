"""Modal deployment for the Edna Search FastAPI backend.

    modal deploy modal_app.py     # deploy; prints the public *.modal.run URL
    modal serve  modal_app.py     # hot-reloading dev deployment

The Next.js frontend (on Vercel) proxies /backend/* to the URL this prints —
set it as CT_SEARCH_BACKEND_URL in the Vercel project. Full walkthrough:
docs/deploy.md.

Cost note: min_containers=1 keeps one container always warm (no cold starts,
and the in-memory run manager + SSE stream stay consistent). That burns compute
continuously — fine on Modal credits. Drop to min_containers=0 to scale to zero
(cheaper, but cold starts return; persisted state survives on the Volume).
"""

from __future__ import annotations

import modal

DATA_DIR = "/data"

# Persistent disk for the SQLite runs DB + telemetry JSONL, so run history and
# the calibration signal survive container recycles and redeploys.
volume = modal.Volume.from_name("edna-data", create_if_missing=True)

# Build the image from the project itself: dependencies from pyproject plus the
# ct_search package (hatchling builds it from src/). README.md is copied because
# pyproject references it as the long-description.
image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("uv")
    .add_local_file("pyproject.toml", "/app/pyproject.toml", copy=True)
    .add_local_file("README.md", "/app/README.md", copy=True)
    .add_local_dir("src", "/app/src", copy=True)
    .run_commands("cd /app && uv pip install --system .")
    .env(
        {
            # Point the app's data files at the mounted Volume.
            "CT_SEARCH_DB_PATH": f"{DATA_DIR}/edna.db",
            "CT_SEARCH_TELEMETRY_PATH": f"{DATA_DIR}/telemetry.jsonl",
        }
    )
)

app = modal.App("edna-search")

# Deploy-specific config + optional provider keys. Create with, e.g.:
#   modal secret create edna-search-secrets \
#       CT_SEARCH_ALLOWED_ORIGINS=https://your-frontend.vercel.app
# Add ANTHROPIC_API_KEY / PARALLEL_API_KEY / ... here to go live; leave them out
# to run in free demo mode. required=False lets the first deploy succeed before
# the secret exists.
secrets = [modal.Secret.from_name("edna-search-secrets", required=False)]


@app.function(
    image=image,
    volumes={DATA_DIR: volume},
    secrets=secrets,
    min_containers=1,  # always-warm: no cold starts; keeps in-memory run state
    timeout=900,  # headroom for streamed multi-step runs
)
@modal.concurrent(max_inputs=100)  # one warm container serves many users at once
@modal.asgi_app()
def web():
    from ct_search.main import app as fastapi_app

    return fastapi_app
