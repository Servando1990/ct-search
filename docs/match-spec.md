# Search & Match — Phase 4 Spec

> Status: **implemented** (2026-06-15). Identity layer (`resolve.py`), thesis
> object + fit scoring (`thesis.py`), the `match` job type and `match_pipeline`
> strategy in the router/executor, dedupe + fit-feedback APIs, and the match
> ledger UI are live. See the implementation-plan table in §3 for per-stage
> landing notes.
> Companions: [spec.md](spec.md) (product spec & status),
> [decision-framework.md](decision-framework.md) (canonical routing rules),
> [overview.md](overview.md) (orientation).

## 0. Thesis

"Match" in Edna means two layered things, and the order matters:

1. **Identity match (infrastructure).** *Is this result about that entity?*
   Entity resolution, record linkage, dedupe. Plumbing — necessary, largely
   commoditizable.
2. **Thesis match (the product).** *Does this entity fit the reason the
   operator is searching?* The operator never wants "100 names with Y
   characteristics" as an end product — the list is an instrument. The actual
   job is a **transaction looking for its counterparty**: a sponsor with a deal
   needs equity, a GP raising Fund III needs LPs, a banker with a sell-side
   mandate needs buyers. The thesis (the deal) is the query; the candidate list
   is supply; match is the scoring function between them.

Thesis match sits **on top of** identity match and is corrupted without it: you
cannot score fit against an entity you haven't resolved, and duplicate
candidates silently shrink a "top 40" shortlist. Build identity first; sell
thesis fit.

### Industry grounding

- **Placement agents are paid for thesis matching.** Knowing which 40 of 400
  LPs have mandate, allocation room, and appetite for *this* strategy/vintage
  is the craft — and reputations die on irrelevant outreach.
- **M&A buyer lists** are the same motion: the teaser is a thesis; buyers are
  scored on sector adjacency, check size, platform-vs-add-on logic, past deals.
- **It has been productized before** (Axial in the LMM; PitchBook/Preqin
  criteria search as the static version). The known weakness everywhere is
  **stale, self-reported mandates**. Recent *behavior* — what a firm actually
  closed, filed, hired for — predicts appetite far better than what its website
  said in 2024. Live-web evidence gathering is exactly what Edna's router is
  for.
- **Failure cost applies to fit, not just citations.** A false positive burns
  relationship capital (you pitched a control buyout to a minority-growth
  fund); a false negative misses the natural buyer. This maps directly onto
  Edna's `evidence_risk` philosophy: the verifier asymmetry extends to match
  claims.

### Why this is the moat

- The **thesis is already in the brief** — the intent parser exists to turn a
  sentence into structured intent; a deal profile is one schema away.
- **Fit scoring is the citation machinery re-aimed**: per-criterion, cited,
  confidence-scored evidence with the criteria as columns.
- The **feedback loop becomes proprietary**: keep/drop on a fit-ranked list is
  the operator teaching Edna their thesis taste; which shortlisted investors
  actually took the meeting is ground truth no static database has. Same
  calibration loop, deeper asset.
- Positioning: Preqin/PitchBook sell *directories*. Edna sells *"the 40 that
  fit this deal, with evidence, ranked, defensible."*

## 1. Product behavior

### 1.1 The match flow (flagship)

```
1. THESIS IN     Operator describes the deal in the composer (or attaches a
                 teaser/one-pager later). Examples:
                 "$8M EBITDA HVAC roll-up in the Southeast, seeking control
                  buyer, $30–60M check" / "First-time $150M industrial
                  services fund, seeking US LPs comfortable with emerging
                  managers"
2. CANDIDATES    Either discovered by Edna (discover route) or uploaded
                 (CSV of investors/LPs/buyers the operator already tracks).
3. RESOLVE       Each candidate is resolved to a canonical identity
                 (domain, CIK/CRD where applicable); list is deduped.
4. EVIDENCE      The router gathers per-criterion evidence per candidate —
                 filings via EDGAR, mandate/activity via web venues — under
                 the normal budget caps and routing rules.
5. SCORE         Each candidate gets a fit score with per-criterion results,
                 citations, and explicit disqualifiers.
6. REVIEW        Ledger ranked by fit; keep/drop; export = the outreach list.
7. LEARN         Keep/drop (and later: meeting/reply outcomes) feed the
                 calibration loop as fit-prior signals.
```

### 1.2 Ledger changes

- New columns: `fit` (composite score), one column per thesis criterion with
  ✓ / ✗ / ? plus citation chips, `match_basis` (domain / CIK / fuzzy 0.83) for
  identity provenance.
- **Disqualifiers are first-class**: "mandate excludes healthcare",
  "check size below floor" — shown red, sortable. Analysts trust a tool that
  says *why not*.
- Upload preview gains a dedupe banner: "3 rows look like the same LP — merge?"

### 1.3 Honesty rules (consistent with the rest of Edna)

- Fit scores carry the same provenance discipline as vendor priors: every
  criterion verdict cites its evidence and shows freshness.
- Unknown is unknown: a criterion with no evidence is `?`, never silently
  scored.
- High `evidence_risk` requires verifier corroboration on disqualifying and
  top-ranked claims before export.

## 2. Architecture

### 2.1 New module: `src/ct_search/resolve.py` (identity layer)

```
resolve_entity(raw: dict) -> ResolvedEntity
  anchors, in priority order:
    1. registry IDs — CIK via SEC company_tickers.json (keyless, EDGAR
       pattern); later CRD via IAPD for advisers/brokers
    2. domain — from an explicit website column, else one cheap routed
       search hit on the official site
    3. normalized name — legal-suffix stripping (LLC/LP/L.P./Ltd/Partners…),
       token-sort, similarity score
  caching: SQLite table `entities` (store.py pattern) — resolve once, ever
```

```
link(candidate: ResolvedEntity, result_row) -> MatchVerdict
  certain   — same CIK or same domain
  probable  — name similarity ≥ threshold_high
  review    — between thresholds (surfaced, never auto-merged)
  distinct  — below threshold_low
MatchVerdict = {level, score, basis, evidence}
```

`_merge_enrichment_rows` / `_apply_enrichment_verification` in providers.py
switch from `_input_key` string equality to `link()` verdicts.

`dedupe(rows) -> clusters` runs at upload preview time; merge decisions are
operator-confirmed, recorded, and fed to telemetry.

### 2.2 New model: the thesis object

```python
class ThesisCriterion(BaseModel):
    key: str                  # "sector_fit", "check_size", "control_appetite"
    description: str          # human-readable test
    weight: float = 1.0
    disqualifying: bool = False

class Thesis(BaseModel):
    kind: Literal["deal_equity", "fund_raise", "sell_side", "custom"]
    summary: str
    criteria: list[ThesisCriterion]
    # structured fields the parser fills when present:
    sector: str | None; geography: str | None
    check_size_usd: tuple[float, float] | None
    structure: str | None     # control / minority / credit / LP commitment
    timeline: str | None
```

- Extracted from the brief by the intent parser (new tool-shaped output beside
  `IntentSignals`); editable in a Tune-style disclosure before the run.
- Persisted with the run (store.py) so a thesis can be re-run against a new
  candidate list ("same deal, fresh supply").

### 2.3 Fit scoring

```
score_candidate(thesis, entity, evidence_rows) -> FitResult
  per criterion: verdict ∈ {pass, fail, unknown} + citations + confidence
  composite = Σ weight·verdict, with any disqualifying fail → capped + flagged
  FitResult = {fit: float, verdicts: [...], disqualifiers: [...], rationale: str}
```

- Criterion verdicts come from an LLM judge over the gathered evidence
  (Claude structured outputs — same pattern as intent.py), **never** from the
  LLM's world knowledge alone; every verdict must point at retrieved evidence
  or return `unknown`.
- Verifier step (existing executor role) re-checks disqualifiers and the top-N
  ranked candidates at high evidence risk.

### 2.4 Routing integration

- New `job_type: "match"` (models.py Literal + intent parser vocabulary:
  "match", "shortlist", "buyer list", "target list", "who should see this
  deal").
- Strategy: `match` jobs compile to a multi-step plan per candidate batch —
  discover (optional) → resolve → per-criterion evidence (routed normally,
  EDGAR for regulatory criteria, web venues for mandate/activity) → judge →
  verify. Budget cap applies per run as today; scoring cost scales with
  candidates × criteria, so `scale_hint`/budget interplay matters and is
  surfaced pre-run ("~$0.04 per candidate at 6 criteria").
- `evidence_risk` semantics extend: high = verifier on disqualifiers + top-N.

### 2.5 Feedback loop

- Outcome payload extends with per-row match feedback:
  `{row_id, fit_shown, kept | dropped, reason?}` and later
  `{contacted, replied, meeting}` when CRM export lands (Phase 5).
- `recompute_scores.py` gains a fit-calibration pass: per-operator thresholds
  and criterion weights drift toward observed keep/meeting rates. Same
  provenance discipline (`usage_telemetry`, sample counts).

## 3. Implementation plan

| Stage | Scope | Status |
|---|---|---|
| **4a — Resolution & linkage** | `resolve.py` (domain + CIK anchors, name normalization), `entities` table, swap executor merging to `link()`, `match_basis` in ledger | ✅ shipped |
| **4b — Dedupe on upload** | `dedupe()` clustering, `POST /api/dedupe` + `/api/dedupe/decision`, decisions recorded to telemetry, upload-preview banner with per-cluster merge / keep-separate | ✅ shipped |
| **4c — Thesis object + fit scoring** | `Thesis` extraction (`thesis.py`), evidence-per-criterion gathering, LLM judge with citation discipline, ranked ledger + disqualifiers + bands | ✅ shipped (live judge needs `ANTHROPIC_API_KEY`; without it criteria read `unknown`) |
| **4d — Fit feedback loop** | `match_feedback` on `UserOutcome`, fit-calibration + linkage passes in `recompute_scores.py` | ✅ shipped |

Eval coverage: `match` routing cases live in `edna_queries.yaml` (54/54 green);
golden resolution/linkage pairs in `tests/test_resolve.py`; fit-scoring band +
disqualifier + unknown-handling assertions in `tests/test_thesis.py`; match-run,
dedupe, and feedback API tests in `tests/test_app.py`. Still open: judge
calibration against human-labeled criterion verdicts (validate-evaluator
methodology) once live-judge traces accumulate.

## 4. Non-goals (for Phase 4)

- No CRM bidirectional sync (export-only until Phase 5).
- No people-data vendors (Apollo/PDL class) — web + filings evidence only.
- No automated outreach. Edna produces the defensible list; humans send it.
- No global entity graph product — resolution cache is per-workspace.

## 5. Open questions

1. Thesis input: brief-only at first, or accept a teaser PDF upload (pdf
   extraction exists in the stack already) in 4c?
2. Should `fit` display as a number (0.82) or band (Strong / Possible / Weak +
   disqualified)? Bands likely read better for outreach lists.
3. Per-candidate evidence budget: flat split of the run budget vs. adaptive
   (spend more on borderline candidates)? Start flat, calibrate later.
4. CRD/IAPD integration timing — keyless but a different system; likely 4a.5
   once Form ADV demand shows up in usage telemetry.
