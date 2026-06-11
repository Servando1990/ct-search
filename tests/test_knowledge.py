"""Calibration overrides — recomputed posteriors must reach the router."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ct_search.provider_knowledge import PROVIDER_KNOWLEDGE, provider_knowledge


def _write_overrides(path: Path, overrides: dict) -> None:
    path.write_text(json.dumps({"overrides": overrides}), encoding="utf-8")


def test_overrides_move_capability_scores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "metric_overrides.json"
    _write_overrides(
        path,
        {
            "brave": {
                "citations": {"prior": 0.66, "observed": 0.9, "samples": 12, "posterior": 0.81}
            }
        },
    )
    monkeypatch.setenv("CT_SEARCH_OVERRIDES_PATH", str(path))

    knowledge = provider_knowledge("brave")
    assert knowledge.capability_scores["citations"] == 0.81
    lead = knowledge.metrics[0]
    assert lead.origin == "usage_telemetry"
    assert lead.axis == "citations"
    assert "12 Edna run outcomes" in lead.notes
    # The in-memory priors stay untouched — overrides are applied per lookup.
    assert PROVIDER_KNOWLEDGE["brave"].capability_scores["citations"] == 0.66


def test_missing_overrides_keep_priors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CT_SEARCH_OVERRIDES_PATH", str(tmp_path / "missing.json"))
    assert provider_knowledge("brave").capability_scores["citations"] == 0.66


def test_malformed_overrides_are_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{this is not json", encoding="utf-8")
    monkeypatch.setenv("CT_SEARCH_OVERRIDES_PATH", str(path))
    assert provider_knowledge("brave").capability_scores["citations"] == 0.66


def test_out_of_range_posteriors_are_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "metric_overrides.json"
    _write_overrides(
        path,
        {"brave": {"citations": {"prior": 0.66, "observed": 9, "samples": 8, "posterior": 7.5}}},
    )
    monkeypatch.setenv("CT_SEARCH_OVERRIDES_PATH", str(path))
    assert provider_knowledge("brave").capability_scores["citations"] == 0.66
