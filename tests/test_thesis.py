from __future__ import annotations

import asyncio

from ct_search.models import (
    CriterionVerdict,
    Evidence,
    ResearchRequest,
    Thesis,
    ThesisCriterion,
)
from ct_search.settings import Settings
from ct_search.thesis import (
    default_thesis,
    judge_candidate,
    resolve_thesis,
    score_candidate,
    verdict_glyph,
)


def _criteria() -> list[ThesisCriterion]:
    return [
        ThesisCriterion(key="sector_fit", description="sector", weight=2.0),
        ThesisCriterion(key="geography_fit", description="geo", weight=1.0),
        ThesisCriterion(
            key="structure_fit", description="structure", disqualifying=True
        ),
    ]


def _thesis() -> Thesis:
    return Thesis(kind="deal_equity", summary="deal", criteria=_criteria())


def _cited(key: str, verdict: str, *, disqualifying: bool = False) -> CriterionVerdict:
    return CriterionVerdict(
        key=key,
        verdict=verdict,
        confidence=0.8,
        citations=[Evidence(title="t", url="https://e.com/1", excerpt="x")],
        disqualifying=disqualifying,
    )


def test_score_all_pass_is_strong() -> None:
    verdicts = [
        _cited("sector_fit", "pass"),
        _cited("geography_fit", "pass"),
        _cited("structure_fit", "pass", disqualifying=True),
    ]
    result = score_candidate(_thesis(), verdicts)
    assert result.fit == 1.0
    assert result.band == "strong"
    assert result.disqualifiers == []
    assert result.known_weight_share == 1.0


def test_disqualifying_fail_caps_and_flags() -> None:
    verdicts = [
        _cited("sector_fit", "pass"),
        _cited("geography_fit", "pass"),
        _cited("structure_fit", "fail", disqualifying=True),
    ]
    result = score_candidate(_thesis(), verdicts)
    assert result.band == "disqualified"
    assert result.fit <= 0.15
    assert result.disqualifiers and "structure_fit" in result.disqualifiers[0]


def test_unknowns_are_excluded_not_scored() -> None:
    # Only the weight-2 sector criterion has evidence; it passes.
    verdicts = [
        _cited("sector_fit", "pass"),
        CriterionVerdict(key="geography_fit", verdict="unknown"),
        CriterionVerdict(key="structure_fit", verdict="unknown", disqualifying=True),
    ]
    result = score_candidate(_thesis(), verdicts)
    assert result.fit == 1.0  # passed weight / known weight, unknowns excluded
    # known share = 2.0 / 4.0 = 0.5; below the strong share floor pushes to possible
    assert result.known_weight_share == 0.5
    assert result.band in ("strong", "possible")


def test_no_evidence_is_weak_not_invented() -> None:
    verdicts = [CriterionVerdict(key=c.key, verdict="unknown") for c in _criteria()]
    result = score_candidate(_thesis(), verdicts)
    assert result.fit == 0.0
    assert result.band == "weak"
    assert result.known_weight_share == 0.0


def test_default_thesis_parses_check_size_band() -> None:
    request = ResearchRequest(
        query="HVAC roll-up seeking control buyer, $30-60M check", mode="search"
    )
    thesis = default_thesis(request)
    assert thesis.check_size_min_usd == 30_000_000
    assert thesis.check_size_max_usd == 60_000_000
    assert thesis.origin == "heuristic"
    assert any(c.disqualifying for c in thesis.criteria)


def test_resolve_thesis_prefers_operator_supplied() -> None:
    supplied = Thesis(kind="custom", summary="mine", criteria=_criteria())
    request = ResearchRequest(query="anything", mode="search", thesis=supplied)
    resolved = asyncio.run(resolve_thesis(request, Settings(anthropic_api_key=None)))
    assert resolved.origin == "operator"
    assert resolved.summary == "mine"


def test_judge_without_key_returns_unknown() -> None:
    thesis = _thesis()
    verdicts = asyncio.run(
        judge_candidate(
            thesis,
            "Some Firm",
            {"source_notes": "x"},
            [Evidence(title="t", url="https://e.com", excerpt="y")],
            Settings(anthropic_api_key=None),
        )
    )
    assert {v.verdict for v in verdicts} == {"unknown"}


def test_judge_demo_mode_is_stable_and_cited() -> None:
    thesis = _thesis()
    settings = Settings(anthropic_api_key=None)
    first = asyncio.run(judge_candidate(thesis, "Atlas", {}, [], settings, demo=True))
    second = asyncio.run(judge_candidate(thesis, "Atlas", {}, [], settings, demo=True))
    assert [v.verdict for v in first] == [v.verdict for v in second]
    for verdict in first:
        if verdict.verdict != "unknown":
            assert verdict.citations  # demo pass/fail still carries a citation


def test_verdict_glyphs() -> None:
    assert verdict_glyph(_cited("k", "pass")).startswith("✓")
    assert verdict_glyph(CriterionVerdict(key="k", verdict="unknown")) == "?"
    fail = verdict_glyph(_cited("k", "fail", disqualifying=True))
    assert fail.startswith("✗") and "disqualifying" in fail
