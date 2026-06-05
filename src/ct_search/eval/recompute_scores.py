"""Nightly score recompute — see docs/decision-framework.md §"Calibration loop".

Reads the JSONL telemetry sink, joins route plans with user outcomes by
`route_plan_id`, and produces an `output/metric_overrides.json` file with
posterior capability scores per (provider, axis).

Update rule (Bayesian-flavored, intentionally simple):

  posterior = (prior_confidence * prior + observed_confidence * observed)
              / (prior_confidence + observed_confidence)

where `observed` is the rolling acceptance rate for that provider on plans
matching the axis (citations → citation_coverage; freshness → 1 - low_confidence_rate
on freshness-sensitive plans; etc.). Anything with too few samples (<5) is
left at its prior so a couple of weird traces can't flip the router.

This is a script, not a service. Cron / GH Actions / a Prefect flow can
invoke it; the framework doesn't dictate the scheduler.

Run:

    uv run python -m ct_search.eval.recompute_scores
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from ct_search.provider_knowledge import PROVIDER_KNOWLEDGE
from ct_search.telemetry import read_telemetry, telemetry_path

OVERRIDES = Path(__file__).resolve().parents[3] / "output" / "metric_overrides.json"
MIN_SAMPLES = 5
OBSERVED_CONFIDENCE_PER_SAMPLE = 0.05  # caps to ~5x prior confidence at n=100


def _join_outcomes(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Build {route_plan_id: {plan + outcome}}."""
    plans: dict[str, dict[str, Any]] = {}
    outcomes: dict[str, dict[str, Any]] = {}
    for row in rows:
        kind = row.get("kind")
        rpid = row.get("route_plan_id")
        if not rpid:
            continue
        if kind == "route_plan":
            plans[rpid] = row
        elif kind == "user_outcome":
            outcomes[rpid] = row.get("user_outcome") or {}
    for rpid, outcome in outcomes.items():
        if rpid in plans:
            plans[rpid]["user_outcome"] = outcome
    return plans


def _observed_per_provider_axis(
    joined: dict[str, dict[str, Any]],
) -> dict[tuple[str, str], list[float]]:
    """Collect observed scores indexed by (provider, capability_axis)."""
    samples: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in joined.values():
        for step in row.get("step_results") or []:
            provider = step.get("provider")
            if not provider:
                continue
            # citation_coverage → citations
            if step.get("returned_rows"):
                samples[(provider, "citations")].append(float(step.get("citation_coverage", 0.0)))
                samples[(provider, "raw_search")].append(
                    1.0 - float(step.get("null_rate", 0.0))
                )
                samples[(provider, "structured_enrichment")].append(
                    float(step.get("avg_confidence", 0.0))
                )
        outcome = row.get("user_outcome") or {}
        accepted = outcome.get("accepted_rows")
        rejected = outcome.get("rejected_rows")
        if accepted is not None and rejected is not None:
            total = accepted + rejected
            if total > 0:
                acceptance = accepted / total
                for step in row.get("step_results") or []:
                    provider = step.get("provider")
                    if provider:
                        # Operator acceptance is the strongest signal we have.
                        samples[(provider, "deep_research")].append(acceptance)
    return samples


def _posterior(prior: float, prior_conf: float, observed: float, observed_conf: float) -> float:
    denom = prior_conf + observed_conf
    if denom <= 0:
        return prior
    return round(
        (prior_conf * prior + observed_conf * observed) / denom,
        4,
    )


def recompute() -> int:
    rows = read_telemetry()
    if not rows:
        print(f"No telemetry rows at {telemetry_path()}. Nothing to recompute.")
        OVERRIDES.parent.mkdir(parents=True, exist_ok=True)
        OVERRIDES.write_text(json.dumps({"overrides": {}}, indent=2), encoding="utf-8")
        return 0
    joined = _join_outcomes(rows)
    samples = _observed_per_provider_axis(joined)

    overrides: dict[str, dict[str, dict[str, float]]] = {}
    for (provider, axis), values in samples.items():
        if len(values) < MIN_SAMPLES:
            continue
        observed = sum(values) / len(values)
        observed_conf = min(
            OBSERVED_CONFIDENCE_PER_SAMPLE * len(values), 0.95
        )
        knowledge = PROVIDER_KNOWLEDGE.get(provider)
        if not knowledge:
            continue
        prior = knowledge.capability_scores.get(axis, 0.5)
        # Default prior confidence: 0.6 for vendor-reported priors.
        posterior = _posterior(prior, 0.6, observed, observed_conf)
        overrides.setdefault(provider, {})[axis] = {
            "prior": prior,
            "observed": round(observed, 4),
            "samples": len(values),
            "posterior": posterior,
        }

    OVERRIDES.parent.mkdir(parents=True, exist_ok=True)
    OVERRIDES.write_text(
        json.dumps(
            {
                "summary": {
                    "telemetry_rows": len(rows),
                    "joined_plans": len(joined),
                    "providers_updated": len(overrides),
                },
                "overrides": overrides,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        f"Recompute: {len(joined)} plans → {len(overrides)} providers updated. "
        f"→ {OVERRIDES}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(recompute())
