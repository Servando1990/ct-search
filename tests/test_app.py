from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Route telemetry to a per-test temporary file before importing app modules.
_TMP_TELEMETRY_DIR = tempfile.mkdtemp(prefix="ct-search-tests-")
os.environ["CT_SEARCH_TELEMETRY_PATH"] = str(Path(_TMP_TELEMETRY_DIR) / "telemetry.jsonl")

from ct_search.main import app  # noqa: E402
from ct_search.models import ResearchRequest, ScaleHint  # noqa: E402
from ct_search.providers import choose_provider  # noqa: E402
from ct_search.settings import Settings  # noqa: E402
from ct_search.telemetry import read_telemetry, telemetry_path  # noqa: E402

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_telemetry_sink() -> None:
    """Each test sees a clean JSONL sink so assertions are deterministic."""
    path = telemetry_path()
    if path.exists():
        path.unlink()
    yield


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
    assert "cited structured enrichment" in providers[0]["best_for"]


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
    assert decision.strategy == "manual"


def test_router_advises_verification_for_cited_enrichment() -> None:
    request = ResearchRequest(
        mode="enrich",
        query=(
            "Enrich placement agent contacts with LinkedIn profiles and recent "
            "fundraising signals. Cite sources."
        ),
        rows=[{"company": "Alpha Capital", "name": "Ada Lane"}],
        fields=["firm", "role", "linkedin_profile", "recent_signal", "source_notes"],
        routing_mode="best",
    )
    decision = choose_provider(request, Settings())
    assert decision.provider == "parallel"
    assert decision.strategy == "primary_with_verification"
    assert decision.prompt_profile["needs_enrichment"] is True
    assert decision.prompt_profile["needs_citations"] is True
    assert decision.steps[0].role == "primary"
    assert any(step.role == "verification" for step in decision.steps)
    assert decision.knowledge_version == "2026-05-21"


# --- PR1 framework: evidence_risk, source_shape, freshness, waterfall --------


def test_high_evidence_risk_requires_cited_provider() -> None:
    """R1: high evidence_risk filters out providers below the citation floor."""
    request = ResearchRequest(
        mode="search",
        query="diligence on placement agent",
        evidence_risk="high",
    )
    decision = choose_provider(request, Settings())
    # Brave (citations 0.66) must NOT be selected under high risk; only Parallel,
    # Exa, and Perplexity clear the 0.85 floor today.
    assert decision.provider in {"parallel", "exa", "perplexity"}
    assert decision.evidence_risk == "high"
    # And the strategy must include a verifier step.
    assert decision.strategy in {"primary_with_verification", "waterfall"}
    assert any(step.role == "verification" for step in decision.steps)


def test_enrichment_at_scale_emits_waterfall_plan() -> None:
    """R6: enrich + rows >= 50 emits a waterfall, not a single provider."""
    request = ResearchRequest(
        mode="enrich",
        job_type="enrich",
        query="Enrich LP contacts with recent commitment signals.",
        rows=[{"company": f"Fund {i}"} for i in range(60)],
        fields=["firm", "sector_focus", "recent_signal"],
        evidence_risk="medium",
    )
    decision = choose_provider(request, Settings())
    assert decision.strategy == "waterfall"
    fallback_steps = [step for step in decision.steps if step.role == "fallback"]
    assert len(fallback_steps) >= 1, "waterfall plan must include at least one fallback"
    # And the scale caveat must be surfaced.
    assert any("waterfall" in caveat.lower() for caveat in decision.caveats)


def test_scale_hint_overrides_inline_rows_for_waterfall_trigger() -> None:
    """R6: scale_hint.rows is authoritative when inline rows are only a sample."""
    request = ResearchRequest(
        mode="enrich",
        job_type="enrich",
        query="Enrich large LP universe (only 5 sample rows attached).",
        rows=[{"company": f"Fund {i}"} for i in range(5)],
        scale_hint=ScaleHint(rows=800),
        evidence_risk="medium",
    )
    decision = choose_provider(request, Settings())
    assert decision.strategy == "waterfall"
    assert any("800" in caveat for caveat in decision.caveats)


def test_similar_to_source_shape_suspends_freshness_penalty() -> None:
    """R3 + F2: similar_to suspends the freshness penalty and biases toward Exa."""
    request = ResearchRequest(
        mode="search",
        query="find more funds like Sequoia and Benchmark",
        source_shape="similar_to",
        freshness_days=3,  # would normally crush Exa
        evidence_risk="medium",
    )
    decision = choose_provider(request, Settings())
    assert decision.provider == "exa"
    assert decision.source_shape == "similar_to"


def test_unsupported_source_shape_surfaces_caveat() -> None:
    """R2 / architecture filter: serp_vertical has no wired provider, so caveat appears."""
    request = ResearchRequest(
        mode="search",
        query="patent landscape for graphene battery startups",
        source_shape="serp_vertical",
        evidence_risk="medium",
    )
    decision = choose_provider(request, Settings())
    assert decision.source_shape == "serp_vertical"
    assert any("SERP" in caveat for caveat in decision.caveats)


def test_back_compat_existing_request_shape() -> None:
    """Existing API callers without the PR1 fields still produce a valid route."""
    request = ResearchRequest(
        mode="search",
        query="placement agent fundraising research",
        routing_mode="best",
    )
    decision = choose_provider(request, Settings())
    # Defaults are applied transparently.
    assert decision.evidence_risk == "medium"
    assert decision.source_shape == "open_web"
    assert decision.job_type in {"research", "brief", "monitor", "discover"}
    assert decision.steps[0].role == "primary"


# --- PR2 framework: provenance, cost-per-grounded-row, processor escalation ---


def test_capability_metrics_carry_provenance() -> None:
    """PR2: ProviderPublic exposes metrics with [vendor-reported, source, date]."""
    response = client.get("/api/providers")
    assert response.status_code == 200
    by_id = {provider["id"]: provider for provider in response.json()}
    parallel = by_id["parallel"]
    assert parallel["metrics"], "Parallel must expose capability metrics with provenance"
    citation_metric = next(
        (metric for metric in parallel["metrics"] if metric["axis"] == "citations"), None
    )
    assert citation_metric is not None
    assert citation_metric["origin"] == "vendor_reported"
    assert citation_metric["source_url"].startswith("https://parallel.ai/")
    assert citation_metric["source_date"].startswith("2026-")
    assert citation_metric["expires_at"].startswith("2026-")
    # Per-provider economics surface for cost_per_grounded_row math.
    assert parallel["avg_tokens_per_result"] == 918
    assert by_id["tavily"]["avg_tokens_per_result"] == 1928


def test_cost_per_grounded_row_includes_downstream_tokens() -> None:
    """PR2: grounded-row cost > per-call cost because tokens + miss-rate are included."""
    request = ResearchRequest(
        mode="enrich",
        job_type="enrich",
        query="Enrich placement-agent contacts.",
        rows=[{"company": "Alpha Capital"}, {"company": "Beta Capital"}],
        fields=["firm", "role"],
        evidence_risk="medium",
    )
    decision = choose_provider(request, Settings())
    assert decision.estimated_cost_per_grounded_row is not None
    assert decision.estimated_cost_per_grounded_row > 0
    # Every step carries its own grounded-row cost.
    primary = next(step for step in decision.steps if step.role == "primary")
    assert primary.estimated_cost_per_grounded_row is not None
    assert primary.estimated_cost_per_grounded_row > 0


def test_tavily_token_density_inflates_grounded_row_cost() -> None:
    """PR2: Tavily's 1928 tok/result vs Parallel's 918 must show in grounded-row cost."""
    base = dict(
        mode="search",
        query="state of the placement-agent market",
        routing_mode="manual",
        evidence_risk="low",
    )
    parallel_decision = choose_provider(
        ResearchRequest(**base, provider="parallel"), Settings()
    )
    tavily_decision = choose_provider(
        ResearchRequest(**base, provider="tavily"), Settings()
    )
    parallel_step = parallel_decision.steps[0]
    tavily_step = tavily_decision.steps[0]
    assert parallel_step.estimated_cost_per_grounded_row is not None
    assert tavily_step.estimated_cost_per_grounded_row is not None
    # Tavily's grounded-row cost must be strictly higher despite a similar nominal
    # per-call price — the token differential is the whole point of the metric.
    tavily_grounded = tavily_step.estimated_cost_per_grounded_row
    parallel_grounded = parallel_step.estimated_cost_per_grounded_row
    assert tavily_grounded > parallel_grounded


def test_processor_escalates_on_depth_signal_not_field_count() -> None:
    """PR2: deep-research prompt signals + high evidence_risk escalate the Parallel processor."""
    shallow_request = ResearchRequest(
        mode="enrich",
        job_type="enrich",
        query="Enrich firms with email status.",
        rows=[{"company": "Alpha Capital"}],
        fields=["email_status", "linkedin_profile"],  # 2 fields → would be 'lite'
        evidence_risk="low",
    )
    deep_request = ResearchRequest(
        mode="enrich",
        job_type="research",
        query=(
            "Build a comprehensive diligence profile with multi-hop reasoning across "
            "competitor websites, regulatory filings, and recent press coverage."
        ),
        rows=[{"company": "Alpha Capital"}],
        fields=["email_status", "linkedin_profile"],  # same 2 fields
        evidence_risk="high",
    )
    shallow = choose_provider(shallow_request, Settings())
    deep = choose_provider(deep_request, Settings())
    # Both run on Parallel under default routing for enrich, so processor_tier is set.
    assert shallow.processor_tier == "lite"
    assert "lite" in shallow.processor_reason
    # Deep request escalates: deep_research signal + high evidence_risk = 2 bumps → core.
    assert deep.processor_tier == "core"
    assert "escalated" in deep.processor_reason


# --- PR3 framework: telemetry & calibration loop -----------------------------


def test_research_response_carries_route_plan_id() -> None:
    response = client.post(
        "/api/research",
        json={
            "mode": "search",
            "query": "placement agent research",
            "routing_mode": "best",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["route_plan_id"].startswith("rp_")


def test_research_call_appends_telemetry_row() -> None:
    response = client.post(
        "/api/research",
        json={
            "mode": "search",
            "query": "diligence on placement agent",
            "evidence_risk": "high",
        },
    )
    assert response.status_code == 200
    rows = read_telemetry()
    plan_rows = [row for row in rows if row.get("kind") == "route_plan"]
    assert len(plan_rows) == 1
    row = plan_rows[0]
    assert row["request_shape"]["evidence_risk"] == "high"
    assert row["plan"]["provider"] in {"parallel", "exa", "perplexity"}
    assert row["step_results"], "primary step result must be recorded"
    assert row["step_results"][0]["role"] == "primary"


def test_user_outcome_endpoint_appends_outcome_row() -> None:
    research = client.post(
        "/api/research",
        json={"mode": "search", "query": "fund-of-funds map"},
    ).json()
    route_plan_id = research["route_plan_id"]
    response = client.post(
        "/api/telemetry/outcome",
        json={
            "route_plan_id": route_plan_id,
            "accepted_rows": 3,
            "rejected_rows": 1,
            "exported": True,
        },
    )
    assert response.status_code == 200
    assert response.json()["recorded"] is True

    outcomes = [row for row in read_telemetry() if row.get("kind") == "user_outcome"]
    assert len(outcomes) == 1
    assert outcomes[0]["route_plan_id"] == route_plan_id
    assert outcomes[0]["user_outcome"]["accepted_rows"] == 3
    assert outcomes[0]["user_outcome"]["exported"] is True


def test_user_outcome_endpoint_rejects_missing_id() -> None:
    response = client.post(
        "/api/telemetry/outcome",
        json={"accepted_rows": 1},
    )
    assert response.status_code == 400


def test_eval_harness_runs_all_cases() -> None:
    from ct_search.eval.run_eval import run_eval

    # Eval writes its scoreboard alongside the repo output dir; just assert it
    # passes end-to-end. Failures in the harness indicate the router drifted
    # from the spec.
    exit_code = run_eval()
    assert exit_code == 0


def test_score_recompute_with_no_data_writes_empty_overrides() -> None:
    # The fixture already cleared the JSONL sink for this test, so recompute
    # should treat it as no-data and exit cleanly.
    from ct_search.eval.recompute_scores import recompute

    exit_code = recompute()
    assert exit_code == 0


# --- PR4 framework: plan executor (walk all steps, not just primary) --------


def test_plan_executor_logs_one_step_result_per_emitted_step() -> None:
    """PR4: every emitted route step produces a StepResult, not just the primary."""
    response = client.post(
        "/api/research",
        json={
            "mode": "search",
            "query": "diligence on placement agent",
            "evidence_risk": "high",  # forces a verifier step
        },
    )
    assert response.status_code == 200
    rows = read_telemetry()
    plan_rows = [row for row in rows if row.get("kind") == "route_plan"]
    assert plan_rows
    step_results = plan_rows[-1]["step_results"]
    roles = [step["role"] for step in step_results]
    # The plan emits primary + verification under high evidence_risk; both must execute.
    assert "primary" in roles
    assert "verification" in roles, (
        f"verifier step was emitted by router but not executed: roles={roles}"
    )


def test_plan_executor_attaches_per_row_step_role() -> None:
    """PR4: each ResultRow carries the step that produced it."""
    response = client.post(
        "/api/research",
        json={
            "mode": "search",
            "query": "placement agent landscape",
            "routing_mode": "best",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["rows"], "executor must still return rows"
    # Every row must carry its step_role; primary step is always present.
    roles = {row["step_role"] for row in data["rows"]}
    assert "primary" in roles or "synthesized" in roles
    # And contributing_providers must include at least one entry.
    assert all(row["contributing_providers"] for row in data["rows"])


def test_waterfall_step_results_track_each_fallback() -> None:
    """PR4: an enrich-at-scale waterfall plan logs primary AND fallback step results."""
    response = client.post(
        "/api/research",
        json={
            "mode": "enrich",
            "job_type": "enrich",
            "query": "Enrich placement-agent contacts at scale.",
            "rows": [{"company": f"Fund {i}"} for i in range(60)],
            "fields": ["firm", "sector_focus", "recent_signal"],
            "evidence_risk": "medium",
        },
    )
    assert response.status_code == 200
    plan_rows = [row for row in read_telemetry() if row.get("kind") == "route_plan"]
    assert plan_rows
    roles = [step["role"] for step in plan_rows[-1]["step_results"]]
    assert "primary" in roles
    assert "fallback" in roles, (
        f"waterfall fallback was emitted but not executed: roles={roles}"
    )


def test_verifier_marks_search_row_when_url_appears_in_both_providers() -> None:
    """PR4: search verification flips `verified` and bumps confidence on URL overlap."""
    from ct_search.models import Evidence, ResultRow
    from ct_search.providers import _apply_search_verification

    base = [
        ResultRow(
            input={"query": "x"},
            fields={"title": "Acme", "url": "https://example.com/acme", "summary": "..."},
            confidence=0.78,
            citations=[Evidence(title="Acme", url="https://example.com/acme", excerpt="")],
            provider="parallel",
            step_role="primary",
            contributing_providers=["parallel"],
        )
    ]
    verifier = [
        ResultRow(
            input={"query": "x"},
            fields={"title": "Acme", "url": "https://example.com/acme", "summary": "..."},
            confidence=0.78,
            citations=[Evidence(title="Acme", url="https://example.com/acme", excerpt="")],
            provider="exa",
            step_role="verified",
        )
    ]
    updated = _apply_search_verification(base, verifier)
    assert updated[0].verified is True
    assert updated[0].confidence > base[0].confidence
    assert "exa" in updated[0].contributing_providers


def test_enrichment_merge_fills_blanks_from_fallback_only() -> None:
    """PR4: merge preserves primary values and only fills blank fields from fallback."""
    from ct_search.models import Evidence, ResultRow
    from ct_search.providers import _merge_enrichment_rows

    primary = [
        ResultRow(
            input={"company": "Alpha Capital"},
            fields={"firm": "Alpha Capital", "recent_signal": ""},
            confidence=0.84,
            citations=[],
            provider="parallel",
            step_role="primary",
            contributing_providers=["parallel"],
        )
    ]
    fallback = [
        ResultRow(
            input={"company": "Alpha Capital"},
            fields={"firm": "WRONG VALUE", "recent_signal": "Closed Fund III in Q1"},
            confidence=0.55,
            citations=[Evidence(title="src", url="https://example.com", excerpt="x")],
            provider="exa",
            step_role="fallback",
        )
    ]
    merged = _merge_enrichment_rows(primary, fallback)
    # Primary value preserved (NOT overwritten by fallback's wrong value).
    assert merged[0].fields["firm"] == "Alpha Capital"
    # Blank field filled by fallback.
    assert merged[0].fields["recent_signal"] == "Closed Fund III in Q1"
    # Fallback provider added to contributors when it filled anything.
    assert "exa" in merged[0].contributing_providers


def test_field_agreement_detects_overlap_for_verifier() -> None:
    """PR4: the agreement heuristic catches semantic overlap, not just exact match."""
    from ct_search.providers import _field_agreement

    primary = {"firm": "Alpha Capital", "sector": "Healthcare buyout"}
    verifier = {
        "firm": "Alpha Capital LP",  # substring match
        "sector": "healthcare buyout fund",  # token overlap
    }
    assert _field_agreement(primary, verifier) >= 0.5


def test_router_uses_speed_fallback_for_fresh_fast_search() -> None:
    # Speed-sensitive monitor-shaped lookup: evidence_risk=low waives the
    # citation floor so a fast index (Brave) wins. See docs/decision-framework.md R1.
    request = ResearchRequest(
        mode="search",
        query="fast latest fundraising news for AI infrastructure companies",
        routing_mode="speed",
        evidence_risk="low",
    )
    decision = choose_provider(request, Settings())
    assert decision.provider == "brave"
    assert decision.strategy == "primary_with_fallback"
    assert decision.prompt_profile["latency_sensitive"] is True
    assert decision.steps[0].provider == "brave"
