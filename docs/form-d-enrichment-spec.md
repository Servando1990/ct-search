# Form D Per-Row Enrichment — Spec

> Status: **implemented** (2026-06-22). Scope: enrich the keyless EDGAR venue so
> `filings` briefs that surface Form D filings carry the structured offering
> data (amount raised, related persons, placement agents) parsed from each
> filing's `primary_doc.xml` — not just filer/date/location metadata.
> Companions: [spec.md](spec.md) (product spec & status),
> [decision-framework.md](decision-framework.md) (canonical routing rules),
> [match-spec.md](match-spec.md) (thesis matching, the primary consumer).

## 0. Thesis

The EDGAR venue today answers *"which entities filed a Form D"* but throws away
the only thing the audience actually asks of a Form D: **how much are they
raising, and who is involved.** [`_edgar_results_to_rows`](../src/ct_search/providers.py)
reads the full-text-search (FTS) index, which returns metadata only — form
type, filer name, file date, CIK, location. The offering amounts, the GPs/
principals, and the paid placement agents live in the filing's structured
`primary_doc.xml`, which FTS never exposes.

A third-party aggregator (formds.com) exists precisely to re-publish that parsed
data. We do not route to it — it is a secondary source with no public API, and
Edna's filings value proposition is *primary-source citations to sec.gov at $0*.
Instead we parse the same primary document ourselves, per row, keeping the
existing sec.gov citation intact and gaining nothing-to-operate freshness.

**Non-goals.** Corpus-wide ranking / screening of *all* Form D filings by amount
(that needs the bulk SEC Form D data sets and a datastore — a separate
"screener" surface, explicitly out of scope here, see §6). This spec only
enriches the rows a brief already surfaces, bounded by `max_results` (≤ 25).

## 1. Product behavior

A `filings` brief that resolves to Form D (e.g. *"Form D filings from healthcare
fund sponsors this quarter"*) returns the same rows it does today, with new
fields populated per row:

| New field | Meaning | Example |
| --- | --- | --- |
| `amount_raised` | Total amount sold to date (USD) | `925000` |
| `total_offering` | Total offering size (USD), or `"Indefinite"` | `"Indefinite"` |
| `total_remaining` | Offering amount still to be sold (USD), or `"Indefinite"` | `50000000` |
| `min_investment` | Minimum investment accepted (USD) | `0` |
| `new_or_amended` | `"new"` or `"amended"` | `"new"` |
| `industry` | Form D industry group | `"Pooled Investment Fund"` |
| `investor_count` | Investors already in | `0` |
| `related_persons` | GPs / executive officers / directors / promoters | `"Matthew Jill (Executive Officer)"` |
| `placement_agents` | Paid sales-compensation recipients (+ CRD when present) | `"Ares Management Capital Markets LLC (CRD 166219)"` |

The `summary` string is extended so the raise size is visible without expanding
the row: e.g. `"D filed 2024-06-27; raised $925,000; Pooled Investment Fund; New York, NY"`.

Behavior is **best-effort and non-blocking**: if the `primary_doc.xml` fetch
fails (403, timeout, 5xx) or parses badly, the base metadata row is returned
unchanged — a brief never fails because enrichment failed. Enrichment runs only
for rows whose `form` is `D` / `D/A`; other form types (13F, 8-K, S-1…) are
untouched.

### Downstream benefit (no extra work here)

`_gather_match_evidence` already folds EDGAR rows into match evidence via
`fields["filings"]`. Once amounts and related persons are on the row, the match
pipeline's thesis criteria (`check_size`, `fund_raise` appetite) can cite a
dollar figure from a primary source instead of inferring one from the web. This
spec does not change the match pipeline; it just makes richer fields available
to it.

## 2. Architecture

One new internal step in the existing EDGAR adapter. No new venue, no new model
(`ResultRow.fields` is `dict[str, Any]`; we add keys, consistent with how
`title` / `url` / `summary` already live there).

```
_edgar_search(request, settings)
  └─ existing: FTS query ladder → hits → _edgar_results_to_rows()  (metadata rows)
  └─ NEW: if any returned row is a Form D, _enrich_form_d_rows(rows, settings)
            └─ bounded-concurrency fetch of each row's primary_doc.xml
            └─ _parse_form_d(xml) → dict of the §1 fields
            └─ merge into row.fields + extend row.summary
```

### 2.1 New functions (`providers.py`, beside the EDGAR block)

```python
def _form_d_doc_url(cik: str, adsh: str) -> str:
    """primary_doc.xml URL — same base path as _edgar_filing_url, doc instead of index."""
    # https://www.sec.gov/Archives/edgar/data/{cik}/{adsh_nodashes}/primary_doc.xml

async def _enrich_form_d_rows(
    rows: list[ResultRow], settings: Settings
) -> list[ResultRow]:
    """Best-effort: fetch + parse each Form D row's primary_doc.xml under a
    semaphore (SEC fair-access ≤ 10 req/s), merge structured fields, never raise."""

def _parse_form_d(xml_text: str) -> dict[str, Any]:
    """Map offeringData / relatedPersonsList / salesCompensationList to §1 fields.
    Pure + synchronous so it is unit-testable against a fixture with no network."""
```

- **Concurrency / rate limit.** A module-level `asyncio.Semaphore(_EDGAR_ENRICH_CONCURRENCY)`
  (default 5) over a single shared `httpx.AsyncClient`, reusing the existing
  `settings.ct_search_edgar_user_agent` header and the `_edgar_fetch` retry/back-off
  posture. At ≤ 25 rows / 5-wide this stays well under SEC's 10 req/s ceiling and
  adds ~1–2 s wall time.
- **Failure isolation.** Per-row fetch/parse wrapped in `try/except`; a failure
  leaves that row at its metadata baseline and increments a telemetry counter.
  `asyncio.gather(..., return_exceptions=True)`.
- **Confidence unchanged** (0.92). The data is richer, not more certain — it is
  the same primary document we already cite.

### 2.2 Settings

| Setting | Default | Purpose |
| --- | --- | --- |
| `ct_search_edgar_enrich_form_d: bool` | `True` | Master switch; `False` restores metadata-only behavior. |
| `ct_search_edgar_enrich_concurrency: int` | `5` | Parallel `primary_doc.xml` fetches. |

### 2.3 Optional follow-up (not in this PR): per-accession cache

Filings are immutable once accepted (an amendment is a new accession number), so
parsed details are safe to cache forever. A `filing_details` SQLite table
(`store.py` pattern, mirroring `resolve.py`'s `entities` cache) keyed by `adsh`
turns repeat briefs into zero-fetch lookups. Deferred to keep this PR small; the
primary path is a live fetch.

## 3. Data mapping (verified against a live filing)

Confirmed against accession `0001947135-24-000001` (Ares Specialty Healthcare
Fund), 2026-06-22. Exact tag paths under `<edgarSubmission>`:

| Row field | XML path | Handling |
| --- | --- | --- |
| `amount_raised` | `offeringData/offeringSalesAmounts/totalAmountSold` | `int`; non-numeric → `None` |
| `total_offering` | `offeringData/offeringSalesAmounts/totalOfferingAmount` | `int`, else pass through `"Indefinite"` |
| `total_remaining` | `offeringData/offeringSalesAmounts/totalRemaining` | `int`, else `"Indefinite"` |
| `min_investment` | `offeringData/minimumInvestmentAccepted` | `int`; absent → `None` |
| `new_or_amended` | `offeringData/typeOfFiling/newOrAmendment/isAmendment` | `"amended"` if `true` else `"new"` |
| `industry` | `offeringData/industryGroup/industryGroupType` | text |
| `investor_count` | `offeringData/investors/totalNumberAlreadyInvested` | `int` |
| `related_persons` | `relatedPersonsList/relatedPersonInfo/*` | `"First Last (Relationship[, …])"`, `; `-joined, capped at ~6 |
| `placement_agents` | `offeringData/salesCompensationList/recipient/recipientName` (+ `recipientCRDNumber` when not `None`) | `"Name (CRD 12345)"`, `; `-joined |

**Critical edge cases the parser must handle** (all observed live):

- `totalOfferingAmount` / `totalRemaining` = literal `"Indefinite"` — never `int()` blindly.
- `totalAmountSold` = `"0"` (yet-to-sell funds) — a real value, distinct from missing.
- Multiple `<issuer>` entries — the primary issuer (`primaryIssuer/entityName`) is the row's filer; the FTS row already carries the display name, so we don't overwrite `title`.
- `relatedPersonsList` may be absent on pooled-fund filings; `salesCompensationList` may be absent — both yield empty strings, not errors.
- `recipientCRDNumber` is frequently the literal `"None"` — suppress the `(CRD …)` suffix in that case.

## 4. Implementation plan

| Step | Change | Lands in |
| --- | --- | --- |
| 1 | `_form_d_doc_url`, `_parse_form_d`, `_enrich_form_d_rows`, concurrency const | `providers.py` |
| 2 | Call `_enrich_form_d_rows` from `_edgar_search` when results contain a Form D | `providers.py` |
| 3 | Extend `summary` with `raised $…` when `amount_raised` present | `providers.py` (`_edgar_results_to_rows` or merge step) |
| 4 | Two settings (§2.2) | `settings.py` |
| 5 | Telemetry: one `logfire.info("edgar_enrich", attempted=…, failed=…)` per batch | `providers.py` (logfire, as in `telemetry.py`) |
| 6 | Tests (§5) | `tests/test_edgar.py` |
| 7 | Docs: note enrichment in `spec.md` filings section + this file → `implemented` | `docs/` |

## 5. Test plan

`_parse_form_d` is pure, so the bulk of coverage is fixture-based and offline.

- **`test_parse_form_d_offering_amounts`** — committed XML fixture (trimmed real
  filing) → asserts `amount_raised == 0`, `total_offering == "Indefinite"`,
  `industry == "Pooled Investment Fund"`, `new_or_amended == "new"`,
  `placement_agents` contains `"Ares Management Capital Markets LLC (CRD 166219)"`,
  and the foreign recipient with `CRD None` has no `(CRD …)` suffix.
- **`test_parse_form_d_numeric_amounts`** — a second fixture with concrete dollar
  amounts and an amendment (`isAmendment=true`) → numeric `amount_raised`,
  `new_or_amended == "amended"`.
- **`test_parse_form_d_missing_sections`** — XML lacking `relatedPersonsList` /
  `salesCompensationList` → empty strings, no exception.
- **`test_parse_form_d_malformed`** — junk/empty string → `{}`, no raise.
- **`test_enrich_skips_non_form_d`** — rows of form `13F-HR` are returned
  unchanged and trigger no fetch (assert via a stub client / call counter).
- **`test_summary_includes_raised_amount`** — merged row's `summary` contains the
  formatted raise.
- Existing `test_edgar_results_parse_into_cited_rows` stays green (metadata path
  unchanged when enrichment is off / pre-merge).

No live-network test in CI; the one-time live verification used to build the
fixtures is documented in the fixture header (date-stamped, as in
`test_edgar.py`).

## 6. Risks & decisions

- **SEC fair-access throttling.** Mitigated by the semaphore + existing UA +
  `_edgar_fetch` back-off. If SEC tightens, lower `ct_search_edgar_enrich_concurrency`.
- **Latency budget.** Adds ~1–2 s to filings briefs. Acceptable for a research
  desk; the master switch and (future) cache bound it.
- **Schema drift.** Form D `schemaVersion` is currently `X0708`; the parser reads
  by tag name with `.get`/guard semantics, tolerant of new optional siblings.
- **Scope discipline.** Anything requiring "rank the whole Form D universe by
  size" is the bulk-ingest screener and is explicitly **out of scope** — see §0
  non-goals.
