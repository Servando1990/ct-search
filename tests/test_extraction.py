"""Extraction route — URL detection, result parsing, shape heuristics."""

from __future__ import annotations

import asyncio

from ct_search.intent import resolve_intent
from ct_search.models import ResearchRequest
from ct_search.providers import _extract_results_to_rows, _extract_urls
from ct_search.settings import Settings


def test_extract_urls_dedupes_and_strips_punctuation() -> None:
    text = (
        "Read https://example.com/fund-page, then https://example.com/fund-page "
        "and https://sec.gov/filing.htm."
    )
    assert _extract_urls(text) == [
        "https://example.com/fund-page",
        "https://sec.gov/filing.htm",
    ]
    assert _extract_urls("no links here") == []


def test_extract_results_become_cited_rows() -> None:
    rows = _extract_results_to_rows(
        [
            {"url": "https://example.com/team", "title": "Team", "text": "Partners: A, B." * 100},
            {"url": "https://example.com/empty", "text": ""},
            {"url": "", "text": "orphan content"},
        ],
        provider="tavily",
    )
    assert len(rows) == 2
    assert rows[0].fields["title"] == "Team"
    assert len(str(rows[0].fields["summary"])) <= 603  # 600 + ellipsis
    assert rows[0].confidence > 0.8
    assert rows[0].citations[0].url == "https://example.com/team"
    # Empty extraction is kept but flagged low-confidence, not silently dropped.
    assert rows[1].confidence < 0.5
    assert rows[1].fields["summary"] == "No extractable content."


def test_url_brief_resolves_known_url_shape_without_llm() -> None:
    request = ResearchRequest(query="Pull the team page from https://example.com/about")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    resolved, origin, _ = asyncio.run(resolve_intent(request, settings))
    assert origin == "heuristic"
    assert resolved.source_shape == "known_url"
