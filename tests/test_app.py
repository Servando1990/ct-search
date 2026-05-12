from __future__ import annotations

import io

from fastapi.testclient import TestClient

from ct_search.main import app
from ct_search.models import ResearchRequest
from ct_search.providers import choose_provider
from ct_search.settings import Settings

client = TestClient(app)


def test_index_loads() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_providers_endpoint() -> None:
    response = client.get("/api/providers")
    assert response.status_code == 200
    providers = response.json()
    assert providers[0]["id"] == "parallel"
    assert "estimated_search_cost" in providers[0]


def test_preview_csv() -> None:
    csv_file = io.BytesIO(b"company,name,title\nAlpha Capital,Ada Lane,Partner\n")
    response = client.post(
        "/api/preview",
        files={"file": ("contacts.csv", csv_file, "text/csv")},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["row_count"] == 1
    assert data["rows"][0]["company"] == "Alpha Capital"


def test_research_demo_enrichment() -> None:
    response = client.post(
        "/api/research",
        json={
            "mode": "enrich",
            "query": "Enrich this placement agent contact list.",
            "rows": [{"company": "Alpha Capital", "name": "Ada Lane"}],
            "fields": ["firm", "role", "source_notes"],
            "routing_mode": "best",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["rows"]
    assert data["is_demo"] is True
    assert "firm" in data["columns"]


def test_export_csv_from_results() -> None:
    research = client.post(
        "/api/research",
        json={
            "mode": "search",
            "query": "placement agent research",
            "routing_mode": "cost",
        },
    ).json()
    response = client.post(
        "/api/export/csv",
        json={
            "title": "Export",
            "columns": research["columns"],
            "rows": research["rows"],
            "route": research["route"],
        },
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "title,url,summary" in response.text


def test_router_honors_manual_provider() -> None:
    request = ResearchRequest(
        mode="search",
        query="fundraising signals",
        routing_mode="manual",
        provider="exa",
    )
    decision = choose_provider(request, Settings())
    assert decision.provider == "exa"
    assert decision.available is False
