"""Entity resolution & record linkage — the Phase 4a identity layer.

"Is this result about that entity?" Everything thesis matching sells sits on
top of this plumbing: you cannot score fit against an entity you haven't
resolved, and duplicate candidates silently shrink a "top 40" shortlist.
See docs/match-spec.md §2.1.

Anchors, in priority order:

  1. registry IDs — a CIK column when present, else a keyless lookup against
     SEC company_tickers.json (the EDGAR pattern: no key, identifying UA).
  2. domain — from an explicit website/url/domain column, else an email host.
  3. normalized name — legal-suffix stripping, token sort, similarity score.

`resolve_local` is pure (no I/O) and powers upload-preview dedupe and the
executor's row linkage. `resolve_entity` adds the registry anchor and the
SQLite `entities` cache — resolve once, ever.
"""

from __future__ import annotations

import re
import threading
from difflib import SequenceMatcher

import httpx
import logfire

from ct_search import store
from ct_search.models import (
    DedupeCluster,
    MatchVerdict,
    ResolvedEntity,
)
from ct_search.settings import Settings

# Linkage thresholds — `review` sits between them and is always surfaced,
# never auto-merged. The 0.83 floor matches the spec's "fuzzy 0.83" ledger
# provenance example.
THRESHOLD_HIGH = 0.92
THRESHOLD_LOW = 0.83

SEC_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

# Legal-entity suffixes carry no identity: "KKR & Co. Inc." is "KKR & Co."
# Strategy words (Capital, Management, Fund) and numerals (Fund III) DO carry
# identity and are never stripped.
_LEGAL_SUFFIXES = frozenset(
    {
        "llc", "lp", "llp", "lllp", "ltd", "limited", "inc", "incorporated",
        "corp", "corporation", "co", "company", "plc", "pllc", "pc",
        "gmbh", "ag", "sa", "sarl", "bv", "nv", "ab", "as", "oy", "spa", "srl",
        "pte", "pty", "kk", "partners", "partner",
    }
)
_NAME_COLUMNS = ("company", "firm", "organization", "investor", "lp", "name", "fund")
_DOMAIN_COLUMNS = ("website", "url", "domain", "homepage", "web", "site")
_CIK_COLUMNS = ("cik", "sec_cik", "cik_number")
_EMAIL_COLUMNS = ("email", "contact_email", "work_email")

_ROMAN_NUMERALS = frozenset(
    {"i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii"}
)


# --- Normalization ------------------------------------------------------------


def normalize_name(raw: str) -> str:
    """Lowercase, drop punctuation and legal suffixes, token-sort the rest."""
    lowered = re.sub(r"[^a-z0-9\s&]", " ", str(raw or "").lower().replace("&", " & "))
    tokens = [token for token in lowered.split() if token != "&"]
    if tokens and tokens[0] == "the":
        tokens = tokens[1:]
    # Suffixes are only suffixes at the tail; "Company X Capital" keeps "company".
    while tokens and tokens[-1] in _LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(sorted(tokens))


def _normalized_ratio(left_norm: str, right_norm: str) -> float:
    """Similarity between two already-normalized names, in [0, 1]."""
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    return SequenceMatcher(None, left_norm, right_norm).ratio()


def name_similarity(left: str, right: str) -> float:
    """Token-sort similarity between two raw names, in [0, 1]."""
    return _normalized_ratio(normalize_name(left), normalize_name(right))


def _numeral_conflict(left: str, right: str) -> bool:
    """True when names differ in fund/series numerals — Fund II is not Fund III."""

    def numerals(name: str) -> set[str]:
        tokens = set(normalize_name(name).split())
        return {t for t in tokens if t in _ROMAN_NUMERALS or t.isdigit()}

    left_nums, right_nums = numerals(left), numerals(right)
    return bool(left_nums or right_nums) and left_nums != right_nums


def extract_domain(value: str) -> str:
    """Registrable host from a URL, bare domain, or email address."""
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if "@" in text and "/" not in text:
        text = text.rsplit("@", 1)[1]
    text = re.sub(r"^[a-z][a-z0-9+.-]*://", "", text)
    host = text.split("/")[0].split("?")[0].split(":")[0]
    host = host.removeprefix("www.")
    return host if "." in host else ""


# --- Resolution ---------------------------------------------------------------


def resolve_local(raw: dict) -> ResolvedEntity:
    """Resolve identity anchors from the row itself. Pure — no network, no cache."""
    name = _first_value(raw, _NAME_COLUMNS)
    cik = _digits(_first_value(raw, _CIK_COLUMNS))
    domain = ""
    for column in _DOMAIN_COLUMNS:
        domain = extract_domain(_column_value(raw, column))
        if domain:
            break
    if not domain:
        domain = _freemail_safe_domain(_first_value(raw, _EMAIL_COLUMNS))
    basis = "cik" if cik else "domain" if domain else "name" if name else "none"
    return ResolvedEntity(
        name=name,
        normalized_name=normalize_name(name),
        domain=domain,
        cik=cik,
        basis=basis,
    )


def resolve_entity(raw: dict, settings: Settings) -> ResolvedEntity:
    """Full resolution: local anchors + SEC registry CIK + the entities cache.

    Cached by identity signals in SQLite (store.py) — resolve once, ever.
    """
    entity = resolve_local(raw)
    if entity.basis == "none":
        return entity
    cache_key = f"{entity.cik}|{entity.domain}|{entity.normalized_name}"
    cached = store.get_entity(cache_key)
    if cached is not None:
        return ResolvedEntity.model_validate(cached)
    if not entity.cik and entity.normalized_name:
        cik = _cik_for_name(entity.normalized_name, settings)
        if cik:
            entity = entity.model_copy(update={"cik": cik, "basis": "cik"})
    store.put_entity(cache_key, entity.model_dump())
    return entity


def describe_basis(entity: ResolvedEntity) -> str:
    """Ledger-facing identity provenance: "cik 1404912" / "domain kkr.com" / "name"."""
    if entity.cik:
        return f"cik {entity.cik}"
    if entity.domain:
        return f"domain {entity.domain}"
    if entity.normalized_name:
        return "name"
    return "unresolved"


# --- Linkage ------------------------------------------------------------------


def link(left: ResolvedEntity, right: ResolvedEntity) -> MatchVerdict:
    """Are these two records the same entity?

      certain  — same CIK or same domain (registry/web anchors are authoritative)
      probable — name similarity ≥ THRESHOLD_HIGH
      review   — between thresholds; surfaced to the operator, never auto-merged
      distinct — below THRESHOLD_LOW, or conflicting registry IDs
    """
    if left.cik and right.cik:
        if left.cik == right.cik:
            return MatchVerdict(
                level="certain", score=1.0, basis="cik", evidence=f"cik {left.cik}"
            )
        return MatchVerdict(
            level="distinct",
            score=0.0,
            basis="cik",
            evidence=f"cik {left.cik} ≠ {right.cik}",
        )
    if left.domain and right.domain and left.domain == right.domain:
        return MatchVerdict(
            level="certain", score=1.0, basis="domain", evidence=f"domain {left.domain}"
        )

    score = _normalized_ratio(left.normalized_name, right.normalized_name)
    evidence = f"name similarity {score:.2f}"
    if score >= THRESHOLD_HIGH:
        level = "probable"
        # "Fund II" vs "Fund III" reads as near-identical text but is a
        # different vehicle — demote to operator review.
        if _numeral_conflict(left.name, right.name):
            level = "review"
            evidence += "; numeral conflict"
    elif score >= THRESHOLD_LOW:
        level = "review"
    else:
        level = "distinct"
    return MatchVerdict(level=level, score=round(score, 4), basis="name", evidence=evidence)


def link_rows(left_row: dict, right_row: dict) -> MatchVerdict:
    """Link two raw input rows by their locally-resolved identities."""
    return link(resolve_local(left_row), resolve_local(right_row))


# --- Dedupe (upload preview) ---------------------------------------------------


def dedupe(rows: list[dict], *, limit: int = 200) -> list[DedupeCluster]:
    """Cluster rows that link as certain/probable/review — suggestions only.

    Union-find over pairwise verdicts at `review` or better. Each cluster
    carries the weakest level inside it, so a chain of one certain and one
    review pair is presented as a review cluster.
    """
    entities = [resolve_local(row) for row in rows[:limit]]
    parent = list(range(len(entities)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    pair_verdicts: dict[tuple[int, int], MatchVerdict] = {}
    for i in range(len(entities)):
        if entities[i].basis == "none":
            continue
        for j in range(i + 1, len(entities)):
            if entities[j].basis == "none":
                continue
            verdict = link(entities[i], entities[j])
            if verdict.level in ("certain", "probable", "review"):
                pair_verdicts[(i, j)] = verdict
                parent[find(i)] = find(j)

    groups: dict[int, list[int]] = {}
    for i in range(len(entities)):
        groups.setdefault(find(i), []).append(i)

    level_rank = {"certain": 0, "probable": 1, "review": 2}
    clusters: list[DedupeCluster] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        verdicts = [
            verdict
            for (i, j), verdict in pair_verdicts.items()
            if i in members and j in members
        ]
        weakest = max(verdicts, key=lambda v: level_rank[v.level])
        clusters.append(
            DedupeCluster(
                row_indices=sorted(members),
                level=weakest.level,
                basis=weakest.basis,
                score=min(v.score for v in verdicts),
                label=entities[members[0]].name or f"rows {sorted(members)}",
                evidence="; ".join(dict.fromkeys(v.evidence for v in verdicts))[:200],
            )
        )
    clusters.sort(key=lambda c: (level_rank[c.level], -c.score))
    return clusters


# --- SEC registry anchor (keyless, EDGAR pattern) -------------------------------

_CIK_INDEX: dict[str, str] | None = None
_CIK_LOCK = threading.Lock()


def _cik_for_name(normalized_name: str, settings: Settings) -> str:
    if not settings.ct_search_entity_registry:
        return ""
    return _cik_index(settings).get(normalized_name, "")


def _cik_index(settings: Settings) -> dict[str, str]:
    """normalized company title → CIK. Fetched once per process; failure caches {}."""
    global _CIK_INDEX
    if _CIK_INDEX is not None:
        return _CIK_INDEX
    with _CIK_LOCK:
        if _CIK_INDEX is not None:
            return _CIK_INDEX
        try:
            response = httpx.get(
                SEC_COMPANY_TICKERS_URL,
                headers={"User-Agent": settings.ct_search_edgar_user_agent},
                timeout=8.0,
            )
            response.raise_for_status()
            data = response.json()
            _CIK_INDEX = {
                normalize_name(item["title"]): str(item["cik_str"])
                for item in data.values()
                if item.get("title") and item.get("cik_str")
            }
            logfire.info("cik_index_loaded {count}", count=len(_CIK_INDEX))
        except Exception as exc:  # noqa: BLE001 — identity must never sink a run
            logfire.warn(
                "cik_index_unavailable {error_type}",
                error_type=type(exc).__name__,
                error=str(exc)[:200],
            )
            _CIK_INDEX = {}
    return _CIK_INDEX


# --- Internals ------------------------------------------------------------------


def _column_value(row: dict, column: str) -> str:
    for key in (column, column.title(), column.upper()):
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _first_value(row: dict, columns: tuple[str, ...]) -> str:
    for column in columns:
        value = _column_value(row, column)
        if value:
            return value
    return ""


def _digits(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    return digits.lstrip("0") if digits else ""


_FREEMAIL = frozenset(
    {"gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com", "aol.com"}
)


def _freemail_safe_domain(email: str) -> str:
    domain = extract_domain(email)
    return "" if domain in _FREEMAIL else domain
