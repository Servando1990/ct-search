"""Async run lifecycle — create, stream-replay, history, recovery."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

# Point persistence at temp files before any ct_search import opens them.
_TMP_DIR = tempfile.mkdtemp(prefix="ct-search-runs-tests-")
os.environ["CT_SEARCH_DB_PATH"] = str(Path(_TMP_DIR) / "edna-test.db")
os.environ.setdefault("CT_SEARCH_TELEMETRY_PATH", str(Path(_TMP_DIR) / "telemetry.jsonl"))

from fastapi.testclient import TestClient  # noqa: E402

from ct_search import store  # noqa: E402
from ct_search.main import app  # noqa: E402
from ct_search.models import ResearchRequest  # noqa: E402

_SEARCH_PAYLOAD = {
    "mode": "search",
    "query": "Map LPs backing lower-middle-market healthcare funds",
    "rows": [],
    "fields": [],
    "routing_mode": "best",
    "provider": None,
    "max_results": 5,
}


def _wait_for_terminal(client: TestClient, run_id: str, timeout_s: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        detail = client.get(f"/api/runs/{run_id}").json()
        if detail["status"] in ("done", "error"):
            return detail
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not finish within {timeout_s}s")


def test_async_run_lifecycle_and_event_replay() -> None:
    with TestClient(app) as client:
        created = client.post("/api/runs", json=_SEARCH_PAYLOAD)
        assert created.status_code == 200
        run_id = created.json()["run_id"]
        assert run_id.startswith("run_")

        detail = _wait_for_terminal(client, run_id)
        assert detail["status"] == "done"
        assert detail["query"] == _SEARCH_PAYLOAD["query"]
        assert detail["response"]["rows"]
        assert detail["response"]["route"]["strategy"]

        # The SSE endpoint replays persisted events and closes at the terminal
        # event, so a plain GET returns the full stream body.
        stream = client.get(f"/api/runs/{run_id}/events")
        assert stream.status_code == 200
        body = stream.text
        assert "run.started" in body
        assert "route.planned" in body
        assert "step.started" in body
        assert "run.completed" in body


def test_run_history_listing() -> None:
    with TestClient(app) as client:
        created = client.post("/api/runs", json=_SEARCH_PAYLOAD)
        run_id = created.json()["run_id"]
        _wait_for_terminal(client, run_id)

        listed = client.get("/api/runs", params={"limit": 5}).json()
        assert any(run["id"] == run_id for run in listed)
        newest = listed[0]
        assert {"id", "status", "query", "mode", "created_at"} <= set(newest)
        # Summaries must stay light — no result rows in the listing.
        assert "response" not in newest


def test_get_run_missing_returns_404() -> None:
    with TestClient(app) as client:
        assert client.get("/api/runs/run_does_not_exist").status_code == 404
        assert client.get("/api/runs/run_does_not_exist/events").status_code == 404


def test_create_run_requires_input() -> None:
    with TestClient(app) as client:
        empty = dict(_SEARCH_PAYLOAD, query="")
        assert client.post("/api/runs", json=empty).status_code == 400


def test_orphaned_runs_marked_errored_on_startup() -> None:
    request = ResearchRequest(query="orphan")
    run_id = store.new_run_id()
    store.create_run(run_id, request)
    store.mark_running(run_id)

    # Entering the TestClient context runs the lifespan recovery.
    with TestClient(app) as client:
        detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["status"] == "error"
    assert "restarted" in detail["error"]
