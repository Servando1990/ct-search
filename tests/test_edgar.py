"""EDGAR filings provider — routing, response parsing, form detection."""

from __future__ import annotations

import asyncio

from ct_search.models import ResearchRequest, ResultRow
from ct_search.providers import (
    _edgar_filing_url,
    _edgar_forms_filter,
    _edgar_results_to_rows,
    _enrich_form_d_rows,
    _form_d_doc_url,
    _merge_form_d_details,
    _parse_form_d,
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


# --- Form D per-row enrichment (docs/form-d-enrichment-spec.md) ---------------

# Trimmed from the live filing used to verify field paths (accession
# 0001947135-24-000001, Ares Specialty Healthcare Fund), 2026-06-22:
# an indefinite pooled-fund offering, yet to sell, two placement-agent
# recipients (one with a CRD, one foreign with CRD "None"), no related persons.
_FORM_D_INDEFINITE = """<?xml version="1.0"?>
<edgarSubmission>
  <submissionType>D</submissionType>
  <primaryIssuer><entityName>Ares Specialty Healthcare Fund (L), L.P.</entityName></primaryIssuer>
  <offeringData>
    <industryGroup><industryGroupType>Pooled Investment Fund</industryGroupType></industryGroup>
    <typeOfFiling><newOrAmendment><isAmendment>false</isAmendment></newOrAmendment></typeOfFiling>
    <minimumInvestmentAccepted>0</minimumInvestmentAccepted>
    <salesCompensationList>
      <recipient>
        <recipientName>DBS Bank Ltd</recipientName>
        <recipientCRDNumber>None</recipientCRDNumber>
      </recipient>
      <recipient>
        <recipientName>Ares Management Capital Markets LLC</recipientName>
        <recipientCRDNumber>166219</recipientCRDNumber>
      </recipient>
    </salesCompensationList>
    <offeringSalesAmounts>
      <totalOfferingAmount>Indefinite</totalOfferingAmount>
      <totalAmountSold>0</totalAmountSold>
      <totalRemaining>Indefinite</totalRemaining>
    </offeringSalesAmounts>
    <investors><totalNumberAlreadyInvested>0</totalNumberAlreadyInvested></investors>
  </offeringData>
</edgarSubmission>"""

# A second shape: concrete dollar amounts, an amendment, and related persons.
_FORM_D_NUMERIC = """<?xml version="1.0"?>
<edgarSubmission>
  <submissionType>D/A</submissionType>
  <primaryIssuer><entityName>Northwind Industrial Partners, LLC</entityName></primaryIssuer>
  <relatedPersonsList>
    <relatedPersonInfo>
      <relatedPersonName><firstName>Jane</firstName><lastName>Doe</lastName></relatedPersonName>
      <relatedPersonRelationshipList>
        <relationship>Executive Officer</relationship>
        <relationship>Director</relationship>
      </relatedPersonRelationshipList>
    </relatedPersonInfo>
    <relatedPersonInfo>
      <relatedPersonName><firstName>John</firstName><lastName>Roe</lastName></relatedPersonName>
      <relatedPersonRelationshipList><relationship>Promoter</relationship></relatedPersonRelationshipList>
    </relatedPersonInfo>
  </relatedPersonsList>
  <offeringData>
    <industryGroup><industryGroupType>Commercial</industryGroupType></industryGroup>
    <typeOfFiling><newOrAmendment><isAmendment>true</isAmendment></newOrAmendment></typeOfFiling>
    <minimumInvestmentAccepted>25000</minimumInvestmentAccepted>
    <offeringSalesAmounts>
      <totalOfferingAmount>5000000</totalOfferingAmount>
      <totalAmountSold>2500000</totalAmountSold>
      <totalRemaining>2500000</totalRemaining>
    </offeringSalesAmounts>
    <investors><totalNumberAlreadyInvested>12</totalNumberAlreadyInvested></investors>
  </offeringData>
</edgarSubmission>"""


def test_form_d_doc_url_builds_primary_doc_path() -> None:
    assert (
        _form_d_doc_url("0001947135", "0001947135-24-000001")
        == "https://www.sec.gov/Archives/edgar/data/1947135/000194713524000001/primary_doc.xml"
    )
    assert _form_d_doc_url("", "") == ""


def test_parse_form_d_indefinite_offering() -> None:
    details = _parse_form_d(_FORM_D_INDEFINITE)
    assert details["amount_raised"] == 0  # yet to sell — a real value, not missing
    assert details["total_offering"] == "Indefinite"
    assert details["total_remaining"] == "Indefinite"
    assert details["min_investment"] == 0
    assert details["new_or_amended"] == "new"
    assert details["industry"] == "Pooled Investment Fund"
    assert details["investor_count"] == 0
    agents = details["placement_agents"]
    assert "Ares Management Capital Markets LLC (CRD 166219)" in agents
    assert "DBS Bank Ltd" in agents and "DBS Bank Ltd (CRD" not in agents  # CRD None suppressed
    assert "related_persons" not in details  # pooled fund lists none


def test_parse_form_d_numeric_amounts_and_amendment() -> None:
    details = _parse_form_d(_FORM_D_NUMERIC)
    assert details["amount_raised"] == 2500000
    assert details["total_offering"] == 5000000
    assert details["min_investment"] == 25000
    assert details["new_or_amended"] == "amended"
    assert details["industry"] == "Commercial"
    assert details["investor_count"] == 12
    persons = details["related_persons"]
    assert "Jane Doe (Executive Officer, Director)" in persons
    assert "John Roe (Promoter)" in persons
    assert "placement_agents" not in details  # no salesCompensationList present


def test_parse_form_d_strips_entity_name_placeholders() -> None:
    # Entity related persons carry a placeholder ("-", "N/A") for the unused
    # first-name half — it must not leak into the rendered name.
    xml = """<?xml version="1.0"?>
<edgarSubmission><offeringData></offeringData>
<relatedPersonsList>
<relatedPersonInfo>
<relatedPersonName><firstName>-</firstName><lastName>Fairmount GP LLC</lastName></relatedPersonName>
<relatedPersonRelationshipList>
<relationship>Executive Officer</relationship>
</relatedPersonRelationshipList>
</relatedPersonInfo>
<relatedPersonInfo>
<relatedPersonName>
<firstName>N/A</firstName><lastName>Meeder Public Funds, Inc.</lastName>
</relatedPersonName>
<relatedPersonRelationshipList>
<relationship>Executive Officer</relationship>
</relatedPersonRelationshipList>
</relatedPersonInfo>
</relatedPersonsList>
</edgarSubmission>"""
    persons = _parse_form_d(xml)["related_persons"]
    assert "Fairmount GP LLC (Executive Officer)" in persons
    assert "Meeder Public Funds, Inc. (Executive Officer)" in persons
    assert "- " not in persons and "N/A" not in persons


def test_parse_form_d_malformed_returns_empty() -> None:
    assert _parse_form_d("") == {}
    assert _parse_form_d("   ") == {}
    assert _parse_form_d("<edgarSubmission><offeringData></offeringData>") == {}  # unclosed
    assert _parse_form_d("<edgarSubmission></edgarSubmission>") == {}  # well-formed, no data


def test_merge_form_d_details_surfaces_raise_in_summary() -> None:
    row = ResultRow(
        fields={"title": "D — Northwind", "summary": "D filed 2026-06-01"},
        provider="edgar",
    )
    _merge_form_d_details(row, {"amount_raised": 2500000, "industry": "Commercial"})
    assert row.fields["industry"] == "Commercial"
    assert row.fields["summary"] == "D filed 2026-06-01; raised $2,500,000"

    yet = ResultRow(fields={"summary": "D filed 2026-06-01"}, provider="edgar")
    _merge_form_d_details(yet, {"amount_raised": 0})
    assert yet.fields["summary"].endswith("yet to sell")


def test_enrich_skips_non_form_d_without_fetching() -> None:
    # 13F rows must never trigger a primary_doc.xml fetch — passing client=None
    # proves the network path is not reached when there are no Form D targets.
    rows = [ResultRow(fields={"title": "13F-HR — Acme"}, provider="edgar")]
    sources = [{"form": "13F-HR", "ciks": ["0000320193"], "adsh": "0000320193-26-000001"}]
    result = asyncio.run(
        _enrich_form_d_rows(rows, sources, Settings(_env_file=None), client=None)  # type: ignore[arg-type]
    )
    assert result is rows
    assert result[0].fields == {"title": "13F-HR — Acme"}  # untouched
