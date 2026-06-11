"""EDGAR filings provider — routing, response parsing, form detection."""

from __future__ import annotations

from ct_search.models import ResearchRequest
from ct_search.providers import (
    _edgar_filing_url,
    _edgar_forms_filter,
    _edgar_results_to_rows,
    choose_provider,
)
from ct_search.settings import Settings

# Trimmed live response from efts.sec.gov (verified 2026-06-11).
_EDGAR_FIXTURE = {
    "hits": {
        "total": {"value": 658, "relation": "eq"},
        "hits": [
            {
                "_id": "0001947135-24-000001:primary_doc.xml",
                "_source": {
                    "ciks": ["0001947135", "0001947134"],
                    "display_names": [
                        "Ares Specialty Healthcare Fund (L), L.P.  (CIK 0001947135)",
                        "Ares Specialty Healthcare Fund (Offshore) (L), L.P.  (CIK 0001947134)",
                    ],
                    "root_forms": ["D"],
                    "file_date": "2024-06-27",
                    "form": "D",
                    "adsh": "0001947135-24-000001",
                    "biz_locations": ["New York, NY", "New York, NY"],
                    "file_type": "D",
                },
            }
        ],
    }
}


def _settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_filings_shape_routes_to_edgar() -> None:
    """R4 — filings jobs go to the primary source, not a web wrapper."""
    request = ResearchRequest(
        query="Form D filings by healthcare funds",
        source_shape="filings",
        evidence_risk="medium",
    )
    decision = choose_provider(request, _settings())
    assert decision.provider == "edgar"
    assert decision.available  # keyless — live without any configured key


def test_open_web_does_not_route_to_edgar() -> None:
    request = ResearchRequest(query="latest fundraising news for industrial software")
    decision = choose_provider(request, _settings())
    assert decision.provider != "edgar"


def test_edgar_results_parse_into_cited_rows() -> None:
    request = ResearchRequest(query="healthcare fund", max_results=5)
    rows = _edgar_results_to_rows(_EDGAR_FIXTURE, request)
    assert len(rows) == 1
    row = rows[0]
    assert row.fields["title"] == "D — Ares Specialty Healthcare Fund (L), L.P."
    assert row.fields["published_date"] == "2024-06-27"
    assert "2 related filers" in str(row.fields["summary"])
    assert (
        row.fields["url"]
        == "https://www.sec.gov/Archives/edgar/data/1947135/000194713524000001/"
        "0001947135-24-000001-index.htm"
    )
    assert row.provider == "edgar"
    assert row.citations and row.citations[0].url == row.fields["url"]
    assert row.confidence > 0.9


def test_edgar_forms_filter_detects_mentions() -> None:
    assert _edgar_forms_filter("Form D filings by sponsors this year") == "D"
    assert _edgar_forms_filter("compare 13F and 8-K activity") == "13F-HR,8-K"
    assert _edgar_forms_filter("general fund research") == ""


def test_edgar_filing_url_handles_missing_parts() -> None:
    assert _edgar_filing_url("", "") == "https://www.sec.gov/edgar/search/"
