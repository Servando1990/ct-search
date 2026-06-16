from __future__ import annotations

from ct_search.resolve import (
    dedupe,
    extract_domain,
    link_rows,
    name_similarity,
    normalize_name,
    resolve_local,
)


def test_normalize_strips_legal_suffixes_and_sorts_tokens() -> None:
    # Legal suffixes carry no identity; strategy words and order do not matter.
    assert normalize_name("KKR & Co. Inc.") == normalize_name("KKR & Co.")
    assert normalize_name("Evergreen Growth Capital LLC") == "capital evergreen growth"


def test_normalize_keeps_fund_numerals() -> None:
    assert "iii" in normalize_name("Insight Partners Fund III").split()
    assert normalize_name("Insight Fund II") != normalize_name("Insight Fund III")


def test_extract_domain_from_url_email_and_bare() -> None:
    assert extract_domain("https://www.KKR.com/about") == "kkr.com"
    assert extract_domain("ada@blackstone.com") == "blackstone.com"
    assert extract_domain("apollo.com") == "apollo.com"
    assert extract_domain("not a domain") == ""


def test_resolve_local_prefers_cik_then_domain_then_name() -> None:
    assert resolve_local({"firm": "X", "cik": "0000320193"}).basis == "cik"
    assert resolve_local({"firm": "X", "website": "x.com"}).basis == "domain"
    assert resolve_local({"firm": "X"}).basis == "name"
    assert resolve_local({"unrelated": ""}).basis == "none"


def test_resolve_local_ignores_freemail_for_domain() -> None:
    entity = resolve_local({"firm": "Solo GP", "email": "person@gmail.com"})
    assert entity.domain == ""
    assert entity.basis == "name"


def test_link_certain_on_shared_domain() -> None:
    verdict = link_rows(
        {"firm": "Pinnacle Industrial Partners", "website": "pinnacle.com"},
        {"firm": "Pinnacle Industrial Partners LLC", "website": "pinnacle.com"},
    )
    assert verdict.level == "certain"
    assert verdict.basis == "domain"
    assert verdict.linked


def test_link_distinct_on_conflicting_cik() -> None:
    verdict = link_rows({"firm": "A", "cik": "111"}, {"firm": "A", "cik": "222"})
    assert verdict.level == "distinct"
    assert not verdict.linked


def test_link_probable_on_near_identical_name() -> None:
    verdict = link_rows(
        {"firm": "Evergreen Growth Capital"},
        {"firm": "Evergreen Growth Capital LLC"},
    )
    assert verdict.level in ("certain", "probable")
    assert verdict.linked


def test_link_fund_numeral_conflict_demoted_to_review() -> None:
    verdict = link_rows(
        {"firm": "Insight Partners Fund II"},
        {"firm": "Insight Partners Fund III"},
    )
    assert verdict.level in ("review", "distinct")
    assert not verdict.linked


def test_link_distinct_on_unrelated_names() -> None:
    assert link_rows({"firm": "Atlas Holdings"}, {"firm": "Meridian Capital"}).level == "distinct"


def test_name_similarity_bounds() -> None:
    assert name_similarity("KKR & Co.", "KKR & Co. Inc.") == 1.0
    assert name_similarity("", "anything") == 0.0


def test_dedupe_clusters_duplicate_rows() -> None:
    rows = [
        {"firm": "Pinnacle Industrial Partners", "website": "pinnacle.com"},
        {"firm": "Evergreen Growth Capital"},
        {"firm": "Pinnacle Industrial Partners LLC", "website": "pinnacle.com"},
    ]
    clusters = dedupe(rows)
    assert len(clusters) == 1
    assert clusters[0].row_indices == [0, 2]
    assert clusters[0].level == "certain"


def test_dedupe_returns_nothing_for_distinct_rows() -> None:
    clusters = dedupe([{"firm": "Atlas Holdings"}, {"firm": "Meridian Capital Group"}])
    assert clusters == []
