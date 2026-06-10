"""Intent resolution semantics — operator wins, LLM fills gaps, heuristics fall back."""

from __future__ import annotations

import asyncio

import pytest

from ct_search import intent
from ct_search.intent import IntentSignals, resolve_intent
from ct_search.models import ResearchRequest
from ct_search.settings import Settings


def _settings(**overrides: object) -> Settings:
    # _env_file=None keeps the developer's .env (and any real keys) out of tests.
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def test_no_key_falls_back_to_heuristic() -> None:
    request = ResearchRequest(query="latest fundraising signals for healthcare funds")
    resolved, origin, note = asyncio.run(resolve_intent(request, _settings()))
    assert origin == "heuristic"
    assert note == ""
    # The resolved request always carries a concrete risk level.
    assert resolved.evidence_risk == "medium"
    # job_type stays unset — choose_provider's keyword inference handles it.
    assert resolved.job_type is None


def test_operator_set_values_skip_inference(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _must_not_be_called(*args: object, **kwargs: object) -> IntentSignals:
        raise AssertionError("infer_intent must not run when everything is set")

    monkeypatch.setattr(intent, "infer_intent", _must_not_be_called)
    request = ResearchRequest(
        query="diligence brief",
        job_type="verify",
        source_shape="filings",
        evidence_risk="high",
        freshness_days=30,
        fields=["firm"],
    )
    resolved, origin, note = asyncio.run(resolve_intent(request, _settings()))
    assert origin == "operator"
    assert note == ""
    assert resolved.job_type == "verify"
    assert resolved.source_shape == "filings"
    assert resolved.evidence_risk == "high"
    assert resolved.freshness_days == 30
    assert resolved.fields == ["firm"]


def test_llm_fills_open_slots(monkeypatch: pytest.MonkeyPatch) -> None:
    signals = IntentSignals(
        job_type="discover",
        source_shape="filings",
        evidence_risk="high",
        freshness_days=90,
        suggested_fields=["Firm Name", "Fund Size (USD)", "IR Contact"],
        reasoning="Diligence-grade discovery over regulatory filings.",
    )

    async def _fake_infer(*args: object, **kwargs: object) -> IntentSignals:
        return signals

    monkeypatch.setattr(intent, "infer_intent", _fake_infer)
    request = ResearchRequest(
        mode="enrich",
        query="map sponsors that filed Form D this quarter",
        rows=[{"company": "Alpha Capital"}],
        fields=[],
    )
    resolved, origin, note = asyncio.run(resolve_intent(request, _settings()))
    assert origin == "llm"
    assert note == "Diligence-grade discovery over regulatory filings."
    assert resolved.job_type == "discover"
    assert resolved.source_shape == "filings"
    assert resolved.evidence_risk == "high"
    assert resolved.freshness_days == 90
    assert resolved.fields == ["firm_name", "fund_size_usd", "ir_contact"]


def test_operator_values_survive_llm_suggestions(monkeypatch: pytest.MonkeyPatch) -> None:
    signals = IntentSignals(job_type="research", evidence_risk="high", reasoning="x")

    async def _fake_infer(*args: object, **kwargs: object) -> IntentSignals:
        return signals

    monkeypatch.setattr(intent, "infer_intent", _fake_infer)
    request = ResearchRequest(query="quick desk scan", evidence_risk="low")
    resolved, origin, _ = asyncio.run(resolve_intent(request, _settings()))
    assert origin == "llm"
    assert resolved.evidence_risk == "low"  # operator wins
    assert resolved.job_type == "research"  # open slot filled


def test_search_mode_ignores_suggested_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    signals = IntentSignals(job_type="research", suggested_fields=["firm", "role"])

    async def _fake_infer(*args: object, **kwargs: object) -> IntentSignals:
        return signals

    monkeypatch.setattr(intent, "infer_intent", _fake_infer)
    request = ResearchRequest(mode="search", query="who backs industrial software?")
    resolved, _, _ = asyncio.run(resolve_intent(request, _settings()))
    assert resolved.fields == []


def test_infer_intent_without_key_returns_none() -> None:
    request = ResearchRequest(query="anything")
    assert asyncio.run(intent.infer_intent(request, _settings())) is None
