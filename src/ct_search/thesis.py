"""Thesis extraction and fit scoring — Phase 4c, the product layer of match.

The operator's job is a transaction looking for its counterparty: the thesis
(the deal) is the query, the candidate list is supply, and fit is the scoring
function between them. See docs/match-spec.md §0.

Three pieces live here:

  extract_thesis  — brief → Thesis via Claude structured outputs (the same
                    pattern as intent.py). Falls back to `default_thesis`.
  judge_candidate — per-criterion verdicts from an LLM judge over *retrieved
                    evidence only*. A verdict without citations is coerced to
                    `unknown`; the judge's world knowledge never scores.
  score_candidate — composite fit: Σ weight·verdict over known criteria, any
                    disqualifying fail caps the score and flags the row.
"""

from __future__ import annotations

import hashlib
import re

import logfire
from pydantic import BaseModel, Field

from ct_search.models import (
    CriterionCall,
    CriterionVerdict,
    Evidence,
    FitBand,
    FitResult,
    ResearchRequest,
    Thesis,
    ThesisCriterion,
)
from ct_search.settings import Settings

THESIS_MAX_TOKENS = 2048
JUDGE_MAX_TOKENS = 2048
LLM_TIMEOUT_SECONDS = 30.0
MAX_CRITERIA = 8

# Any disqualifying fail caps the composite here — the row sinks to the bottom
# of the ledger but stays visible with its reason ("why not" builds trust).
DISQUALIFIED_FIT_CAP = 0.15
STRONG_FIT_FLOOR = 0.75
POSSIBLE_FIT_FLOOR = 0.45
# A "strong" call additionally needs evidence behind at least half the
# criterion weight; unknowns never quietly inflate a score.
STRONG_KNOWN_SHARE_FLOOR = 0.5

THESIS_SYSTEM = """\
You parse deal briefs for Edna Search, a research desk for private-capital
teams. The operator describes a transaction looking for its counterparty: a
sponsor with a deal seeking equity, a GP raising a fund seeking LPs, a banker
with a sell-side mandate seeking buyers. Turn the brief into a thesis the
match engine can score candidates against.

kind — deal_equity (a deal seeking investors/buyers of a stake),
fund_raise (a fund seeking LP commitments), sell_side (a sale mandate seeking
acquirers), custom (anything else).

summary — one sentence restating the transaction.

criteria — 3–8 testable criteria, each phrased so an analyst could mark
pass/fail from public evidence about ONE candidate. Use snake_case keys
(sector_fit, geography_fit, check_size, structure_fit, mandate_room,
emerging_manager_appetite, recent_activity, ...). Mark a criterion
disqualifying ONLY when a fail makes outreach embarrassing (wrong structure,
mandate exclusion, check size out of range). Weight 1.0 by default; raise to
1.5–2.0 for the criteria the brief stresses.

Structured fields (sector, geography, check_size_min_usd, check_size_max_usd,
structure, timeline) — fill only what the brief states; never guess. Check
sizes are absolute USD (e.g. "$30–60M" → 30000000 / 60000000).
"""

JUDGE_SYSTEM = """\
You are the fit judge for Edna Search. You receive ONE candidate, a deal
thesis with criteria, and the evidence Edna retrieved about that candidate
(fields and cited excerpts). For each criterion return a verdict:

- pass — the retrieved evidence supports fit. Cite evidence indices.
- fail — the retrieved evidence contradicts fit. Cite evidence indices.
- unknown — the evidence retrieved here neither supports nor contradicts.

Hard rules:
- Judge ONLY from the evidence provided. Your background knowledge of the
  candidate must not produce a pass or fail — if the evidence is silent,
  return unknown, even when you are personally confident.
- Every pass/fail must list at least one evidence_indices entry.
- note: one short clause an analyst can read in the ledger ("mandate page
  lists industrials", "minimum check $50M exceeds range").
"""


# --- Thesis extraction ----------------------------------------------------------


class _JudgeVerdict(BaseModel):
    key: str
    verdict: CriterionCall
    evidence_indices: list[int] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    note: str = ""


class _JudgeOutput(BaseModel):
    verdicts: list[_JudgeVerdict]


async def extract_thesis(request: ResearchRequest, settings: Settings) -> Thesis | None:
    """Ask Claude for the deal profile. Returns None when unavailable."""
    if not settings.anthropic_api_key or not request.query.strip():
        return None
    try:
        import anthropic

        client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key,
            timeout=LLM_TIMEOUT_SECONDS,
            max_retries=1,
        )
        response = await client.messages.parse(
            model=settings.ct_search_intent_model,
            max_tokens=THESIS_MAX_TOKENS,
            system=THESIS_SYSTEM,
            messages=[{"role": "user", "content": f"Brief: {request.query.strip()}"}],
            output_format=Thesis,
        )
        thesis = response.parsed_output
        thesis.origin = "llm"
        thesis.criteria = thesis.criteria[:MAX_CRITERIA]
        logfire.info(
            "thesis_extracted {kind} {criteria_count}",
            kind=thesis.kind,
            criteria_count=len(thesis.criteria),
        )
        return thesis
    except Exception as exc:  # noqa: BLE001 — thesis extraction must never sink a run
        logfire.warn(
            "thesis_extract_failed {error_type}",
            error_type=type(exc).__name__,
            error=str(exc)[:300],
        )
        return None


_CHECK_SIZE_PATTERN = re.compile(
    r"\$\s*(\d+(?:\.\d+)?)\s*(?:m|mm|million)?\s*(?:-|–|—|to)\s*"
    r"\$?\s*(\d+(?:\.\d+)?)\s*(m|mm|million|b|bn|billion)",
    re.IGNORECASE,
)


def default_thesis(request: ResearchRequest) -> Thesis:
    """Keyword fallback when no key or extraction fails — honest, generic criteria.

    The descriptions echo the brief verbatim so the judge tests the operator's
    words, not an invented mandate.
    """
    brief = request.query.strip() or "the attached deal brief"
    lowered = brief.lower()
    if any(term in lowered for term in ("lp", "fund i", "raising", "commitment", "first-time")):
        kind = "fund_raise"
    elif any(term in lowered for term in ("sell-side", "mandate", "acquirer", "buyer")):
        kind = "sell_side"
    elif any(term in lowered for term in ("buyout", "check", "equity", "roll-up", "platform")):
        kind = "deal_equity"
    else:
        kind = "custom"

    check_min = check_max = None
    match = _CHECK_SIZE_PATTERN.search(brief)
    if match:
        unit = 1e9 if match.group(3).lower().startswith("b") else 1e6
        check_min, check_max = float(match.group(1)) * unit, float(match.group(2)) * unit

    criteria = [
        ThesisCriterion(
            key="sector_fit",
            description=f"Invests or transacts in the sector described: {brief}",
            weight=1.5,
        ),
        ThesisCriterion(
            key="geography_fit",
            description=f"Active in the geography described: {brief}",
        ),
        ThesisCriterion(
            key="structure_fit",
            description=f"Pursues the deal structure described: {brief}",
            disqualifying=True,
        ),
        ThesisCriterion(
            key="check_size",
            description=(
                f"Typical commitment fits ${check_min / 1e6:.0f}–{check_max / 1e6:.0f}M"
                if check_min and check_max
                else f"Typical commitment fits the size described: {brief}"
            ),
            disqualifying=True,
        ),
        ThesisCriterion(
            key="recent_activity",
            description="Closed, filed, or announced comparable activity in the last 24 months",
        ),
    ]
    return Thesis(
        kind=kind,
        summary=brief[:300],
        criteria=criteria,
        check_size_min_usd=check_min,
        check_size_max_usd=check_max,
        origin="heuristic",
    )


async def resolve_thesis(
    request: ResearchRequest, settings: Settings
) -> Thesis:
    """Operator-supplied thesis wins; else extract with Claude; else keyword fallback."""
    if request.thesis is not None and request.thesis.criteria:
        thesis = request.thesis.model_copy(deep=True)
        thesis.origin = "operator"
        return thesis
    extracted = await extract_thesis(request, settings)
    return extracted if extracted is not None else default_thesis(request)


# --- The judge ------------------------------------------------------------------


async def judge_candidate(
    thesis: Thesis,
    candidate_label: str,
    fields: dict,
    citations: list[Evidence],
    settings: Settings,
    *,
    demo: bool = False,
) -> list[CriterionVerdict]:
    """Per-criterion verdicts for one candidate, from retrieved evidence only.

    Without an ANTHROPIC key the honest answer is `unknown` everywhere —
    except in demo mode, where stable sample verdicts keep the keyless
    walkthrough legible (the run is already flagged `is_demo`).
    """
    if demo:
        return _demo_verdicts(thesis, candidate_label)
    if not settings.anthropic_api_key:
        return [
            _unknown(criterion, "no fit judge available (ANTHROPIC_API_KEY unset)")
            for criterion in thesis.criteria
        ]
    try:
        import anthropic

        client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key,
            timeout=LLM_TIMEOUT_SECONDS,
            max_retries=1,
        )
        response = await client.messages.parse(
            model=settings.ct_search_judge_model,
            max_tokens=JUDGE_MAX_TOKENS,
            system=JUDGE_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": _judge_prompt(thesis, candidate_label, fields, citations),
                }
            ],
            output_format=_JudgeOutput,
        )
        return _verdicts_from_judge(thesis, response.parsed_output, citations)
    except Exception as exc:  # noqa: BLE001 — a judge error must not sink the run
        logfire.warn(
            "fit_judge_failed {error_type}",
            error_type=type(exc).__name__,
            error=str(exc)[:300],
        )
        return [_unknown(criterion, "judge call failed") for criterion in thesis.criteria]


def _judge_prompt(
    thesis: Thesis, candidate_label: str, fields: dict, citations: list[Evidence]
) -> str:
    lines = [f"Thesis ({thesis.kind}): {thesis.summary}", "", "Criteria:"]
    for criterion in thesis.criteria:
        flag = " [disqualifying]" if criterion.disqualifying else ""
        lines.append(f"- {criterion.key}: {criterion.description}{flag}")
    lines += ["", f"Candidate: {candidate_label}", "", "Retrieved fields:"]
    for key, value in fields.items():
        if value not in (None, "", []):
            lines.append(f"- {key}: {str(value)[:300]}")
    lines += ["", "Evidence excerpts (cite by index):"]
    if citations:
        for index, citation in enumerate(citations):
            lines.append(
                f"[{index}] {citation.title} — {citation.url} — {citation.excerpt[:280]}"
            )
    else:
        lines.append("(none retrieved — every verdict must be unknown)")
    return "\n".join(lines)


def _verdicts_from_judge(
    thesis: Thesis, output: _JudgeOutput, citations: list[Evidence]
) -> list[CriterionVerdict]:
    by_key = {item.key: item for item in output.verdicts}
    verdicts: list[CriterionVerdict] = []
    for criterion in thesis.criteria:
        raw = by_key.get(criterion.key)
        if raw is None:
            verdicts.append(_unknown(criterion, "judge returned no verdict"))
            continue
        cited = [citations[i] for i in raw.evidence_indices if 0 <= i < len(citations)]
        verdict = raw.verdict
        note = raw.note
        if verdict != "unknown" and not cited:
            # Honesty rule: an uncited pass/fail is world knowledge — reject it.
            verdict, note = "unknown", "judge verdict lacked citations; discarded"
        verdicts.append(
            CriterionVerdict(
                key=criterion.key,
                verdict=verdict,
                confidence=raw.confidence if verdict != "unknown" else 0.0,
                citations=cited[:3],
                note=note[:200],
                disqualifying=criterion.disqualifying,
            )
        )
    return verdicts


def _unknown(criterion: ThesisCriterion, note: str) -> CriterionVerdict:
    return CriterionVerdict(
        key=criterion.key,
        verdict="unknown",
        note=note,
        disqualifying=criterion.disqualifying,
    )


def _demo_verdicts(thesis: Thesis, candidate_label: str) -> list[CriterionVerdict]:
    """Stable sample verdicts for the keyless walkthrough — clearly demo-cited."""
    verdicts: list[CriterionVerdict] = []
    for criterion in thesis.criteria:
        digest = hashlib.sha1(f"{candidate_label}:{criterion.key}".encode()).hexdigest()
        bucket = int(digest[:2], 16)
        verdict: CriterionCall = (
            "pass" if bucket < 140 else "unknown" if bucket < 210 else "fail"
        )
        citation = Evidence(
            title=f"Demo evidence for {candidate_label}",
            url=f"https://example.com/demo/{digest[:8]}",
            excerpt=f"Demo verdict on {criterion.key}; connect keys for live cited judging.",
        )
        verdicts.append(
            CriterionVerdict(
                key=criterion.key,
                verdict=verdict,
                confidence=0.6 if verdict != "unknown" else 0.0,
                citations=[citation] if verdict != "unknown" else [],
                note="demo verdict",
                disqualifying=criterion.disqualifying,
            )
        )
    return verdicts


# --- Composite scoring -----------------------------------------------------------


def score_candidate(thesis: Thesis, verdicts: list[CriterionVerdict]) -> FitResult:
    """Σ weight·verdict over criteria with evidence; disqualifying fails cap + flag.

    Unknowns are excluded from the composite rather than scored — a thin
    evidence base shows up as low `known_weight_share`, not a fake number.
    """
    weights = {criterion.key: criterion.weight for criterion in thesis.criteria}
    total_weight = sum(weights.values()) or 1.0
    known_weight = 0.0
    passed_weight = 0.0
    disqualifiers: list[str] = []
    for verdict in verdicts:
        weight = weights.get(verdict.key, 1.0)
        if verdict.verdict == "unknown":
            continue
        known_weight += weight
        if verdict.verdict == "pass":
            passed_weight += weight
        elif verdict.disqualifying:
            disqualifiers.append(f"{verdict.key}: {verdict.note or 'failed'}")

    fit = round(passed_weight / known_weight, 4) if known_weight else 0.0
    known_share = round(known_weight / total_weight, 4)
    if disqualifiers:
        fit = min(fit, DISQUALIFIED_FIT_CAP)
        band: FitBand = "disqualified"
    elif fit >= STRONG_FIT_FLOOR and known_share >= STRONG_KNOWN_SHARE_FLOOR:
        band = "strong"
    elif fit >= POSSIBLE_FIT_FLOOR and known_share > 0:
        band = "possible"
    else:
        band = "weak"

    return FitResult(
        fit=fit,
        band=band,
        verdicts=verdicts,
        disqualifiers=disqualifiers,
        known_weight_share=known_share,
        rationale=_rationale(band, fit, known_share, disqualifiers),
    )


def _rationale(
    band: FitBand, fit: float, known_share: float, disqualifiers: list[str]
) -> str:
    if disqualifiers:
        return f"Disqualified: {disqualifiers[0]}"
    if known_share == 0:
        return "No criterion could be scored from retrieved evidence."
    return (
        f"{band.capitalize()} fit ({fit:.2f}) with evidence behind "
        f"{known_share:.0%} of criterion weight."
    )


def verdict_glyph(verdict: CriterionVerdict) -> str:
    """Ledger cell for one criterion: ✓ / ✗ / ? with confidence when judged."""
    if verdict.verdict == "pass":
        return f"✓ {verdict.confidence:.2f}".rstrip()
    if verdict.verdict == "fail":
        glyph = f"✗ {verdict.confidence:.2f}"
        return f"{glyph} (disqualifying)" if verdict.disqualifying else glyph
    return "?"
