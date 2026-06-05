"""Edna-native eval harness — see docs/decision-framework.md §"Calibration loop".

Loads the YAML manifest, builds a `ResearchRequest` for each case, runs the
router (no vendor API calls), and asserts the emitted plan matches the
expected shape. The harness writes:

  - A scoreboard JSON to `output/eval_scoreboard.json` summarizing pass/fail.
  - One telemetry row per case to the standard JSONL sink (so the recompute
    job can be exercised end-to-end).

Run with:

    uv run python -m ct_search.eval.run_eval
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ct_search.models import ResearchRequest
from ct_search.providers import choose_provider
from ct_search.settings import Settings
from ct_search.telemetry import (
    configure_logfire,
    log_route_plan,
    new_route_plan_id,
)

MANIFEST = Path(__file__).resolve().parent / "edna_queries.yaml"
SCOREBOARD = Path(__file__).resolve().parents[3] / "output" / "eval_scoreboard.json"


@dataclass
class CaseResult:
    id: str
    description: str
    passed: bool
    provider: str
    strategy: str
    caveats: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


def _load_manifest() -> list[dict[str, Any]]:
    with MANIFEST.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return data.get("cases", [])


def _check_expectations(decision, expect: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    provider_in = expect.get("provider_in")
    if provider_in and decision.provider not in provider_in:
        failures.append(f"provider {decision.provider!r} not in {provider_in!r}")

    strategy = expect.get("strategy")
    if strategy and strategy != "any" and decision.strategy != strategy:
        failures.append(f"strategy {decision.strategy!r} != {strategy!r}")

    roles_required = expect.get("role_present") or []
    roles_actual = {step.role for step in decision.steps}
    for role in roles_required:
        if role not in roles_actual:
            failures.append(f"role {role!r} missing from plan (have {sorted(roles_actual)})")

    caveat_match = expect.get("caveat_match")
    if caveat_match:
        if not any(caveat_match.lower() in caveat.lower() for caveat in decision.caveats):
            failures.append(
                f"caveat with substring {caveat_match!r} not found "
                f"(have {decision.caveats!r})"
            )
    return failures


def run_eval() -> int:
    configure_logfire()
    settings = Settings()
    cases = _load_manifest()
    results: list[CaseResult] = []
    for case in cases:
        request_payload = case["request"]
        # Strip non-API helpers if present.
        request = ResearchRequest.model_validate(request_payload)
        decision = choose_provider(request, settings)
        failures = _check_expectations(decision, case.get("expect", {}))
        results.append(
            CaseResult(
                id=case["id"],
                description=case.get("description", ""),
                passed=not failures,
                provider=decision.provider,
                strategy=decision.strategy,
                caveats=list(decision.caveats),
                failures=failures,
            )
        )
        # Also log a telemetry row so the calibration job sees these plans.
        log_route_plan(
            route_plan_id=new_route_plan_id(),
            request=request,
            decision=decision,
        )

    passed = sum(1 for result in results if result.passed)
    total = len(results)
    SCOREBOARD.parent.mkdir(parents=True, exist_ok=True)
    SCOREBOARD.write_text(
        json.dumps(
            {
                "summary": {"passed": passed, "total": total},
                "cases": [
                    {
                        "id": result.id,
                        "description": result.description,
                        "passed": result.passed,
                        "provider": result.provider,
                        "strategy": result.strategy,
                        "caveats": result.caveats,
                        "failures": result.failures,
                    }
                    for result in results
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"Eval: {passed}/{total} cases passed. Scoreboard → {SCOREBOARD}")
    for result in results:
        marker = "✓" if result.passed else "✗"
        print(f"  {marker} {result.id} → {result.provider} / {result.strategy}")
        for failure in result.failures:
            print(f"      ! {failure}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(run_eval())
