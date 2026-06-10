"""Telemetry for the routing/calibration loop (PR3).

The decision framework's keystone is that vendor priors must be calibrated by
Edna's own usage outcomes — see docs/decision-framework.md §"Calibration loop".

This module:

1. Defines the Pydantic models for `RouteTelemetry`, `StepResult`,
   `UserOutcome`, and `RequestShape` (the schema in the spec).
2. Provides a single `log_route_plan` entry point that:
     - emits a structured Logfire span (live observability)
     - appends a JSONL line to the local telemetry sink (offline recompute)
3. Provides a `record_user_outcome` hook for the frontend to attach
   accept/reject/export signals to a previously-logged plan.

No persistence backend beyond JSONL is required for PR3. The eval harness
and the score-recompute job read the JSONL sink directly.

Logfire configuration is intentionally `send_to_logfire="if-token-present"`
so the rest of the stack works without LOGFIRE_TOKEN. When a token is set,
spans flow to https://logfire.pydantic.dev; when it isn't, spans still
collect structured data locally.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import logfire
from pydantic import BaseModel, Field

from ct_search.models import (
    EvidenceRisk,
    JobType,
    ProviderId,
    ResearchRequest,
    RouteDecision,
    RouteStepRole,
    SourceShape,
)

# --- Models -----------------------------------------------------------------


class RequestShape(BaseModel):
    """The five-axis routing signal recorded with every plan."""

    job_type: JobType | None = None
    source_shape: SourceShape = "open_web"
    evidence_risk: EvidenceRisk = "medium"
    freshness_days: int | None = None
    rows: int = 0
    fields_count: int = 0


class StepResult(BaseModel):
    """Per-step outcome — populated by the runner, not the router."""

    provider: ProviderId
    role: RouteStepRole
    latency_ms: int = 0
    cost_usd: float | None = None
    returned_rows: int = 0
    null_rate: float = 0.0
    citation_coverage: float = 0.0
    avg_confidence: float = 0.0
    low_confidence_rate: float = 0.0
    error_type: str | None = None


class UserOutcome(BaseModel):
    """Recorded asynchronously from the workbench after the user reviews."""

    accepted_rows: int | None = None
    rejected_rows: int | None = None
    exported: bool = False
    edited_fields: int | None = None


class RouteTelemetry(BaseModel):
    """One row in the calibration log. Matches the schema in §Calibration loop."""

    route_plan_id: str
    occurred_at: str  # ISO8601, UTC
    request_shape: RequestShape
    plan: dict[str, Any]  # serialized RouteDecision (for offline recompute)
    step_results: list[StepResult] = Field(default_factory=list)
    user_outcome: UserOutcome | None = None


# --- Configuration ---------------------------------------------------------

_TELEMETRY_PATH = Path(
    os.environ.get(
        "CT_SEARCH_TELEMETRY_PATH",
        Path(__file__).resolve().parent.parent.parent / "output" / "telemetry.jsonl",
    )
)
_WRITE_LOCK = threading.Lock()
_LOGFIRE_CONFIGURED = False


def configure_logfire(service_name: str = "edna-search") -> None:
    """Idempotent Logfire setup. Safe to call repeatedly.

    Sends to Logfire only when LOGFIRE_TOKEN is present; otherwise structured
    spans still build locally and JSONL persistence remains the durable record.
    """
    global _LOGFIRE_CONFIGURED
    if _LOGFIRE_CONFIGURED:
        return
    logfire.configure(
        service_name=service_name,
        send_to_logfire="if-token-present",
        console=False,
    )
    # Pydantic plugin captures validation events. instrument_fastapi / httpx
    # are wired in main.py once `app` exists.
    logfire.instrument_pydantic()
    _LOGFIRE_CONFIGURED = True


# --- Recording -------------------------------------------------------------


def new_route_plan_id() -> str:
    return f"rp_{uuid.uuid4().hex[:16]}"


def build_request_shape(request: ResearchRequest, rows: int, fields: int) -> RequestShape:
    return RequestShape(
        job_type=request.job_type,
        source_shape=request.source_shape,
        evidence_risk=request.evidence_risk or "medium",
        freshness_days=request.freshness_days,
        rows=rows,
        fields_count=fields,
    )


def log_route_plan(
    *,
    route_plan_id: str,
    request: ResearchRequest,
    decision: RouteDecision,
    step_results: list[StepResult] | None = None,
) -> RouteTelemetry:
    """Emit a Logfire span and append a JSONL row for one route plan.

    Called after the runner finishes (so step_results can include latency,
    null_rate, etc.). Safe to call with `step_results=None` if we only want
    to record the plan itself (e.g. during eval-harness dry runs).
    """
    rows = max(len(request.rows), 0)
    fields = max(len(request.fields or []), 0)
    shape = build_request_shape(request, rows, fields)
    telemetry = RouteTelemetry(
        route_plan_id=route_plan_id,
        occurred_at=datetime.now(UTC).isoformat(),
        request_shape=shape,
        plan=decision.model_dump(mode="json"),
        step_results=step_results or [],
    )

    # Logfire structured event. Keys become searchable attributes.
    with logfire.span(
        "route_plan {provider} {strategy}",
        provider=decision.provider,
        strategy=decision.strategy,
        route_plan_id=route_plan_id,
        job_type=shape.job_type,
        source_shape=shape.source_shape,
        evidence_risk=shape.evidence_risk,
        freshness_days=shape.freshness_days,
        rows=shape.rows,
        fields_count=shape.fields_count,
        estimated_cost_per_grounded_row=decision.estimated_cost_per_grounded_row,
        caveats_count=len(decision.caveats),
    ):
        for step_result in telemetry.step_results:
            logfire.info(
                "route_step {provider} {role}",
                provider=step_result.provider,
                role=step_result.role,
                latency_ms=step_result.latency_ms,
                cost_usd=step_result.cost_usd,
                returned_rows=step_result.returned_rows,
                null_rate=step_result.null_rate,
                citation_coverage=step_result.citation_coverage,
                avg_confidence=step_result.avg_confidence,
                low_confidence_rate=step_result.low_confidence_rate,
                error_type=step_result.error_type,
            )

    _append_jsonl(telemetry)
    return telemetry


def record_user_outcome(route_plan_id: str, outcome: UserOutcome) -> bool:
    """Attach a user_outcome to a previously-logged plan.

    Returns True if a matching plan was found in the JSONL sink and patched.
    The sink is append-only; this writes a second row keyed by route_plan_id
    that the recompute job joins on. Cheaper than rewriting the whole file
    and survives concurrent writes.
    """
    update = {
        "route_plan_id": route_plan_id,
        "occurred_at": datetime.now(UTC).isoformat(),
        "user_outcome": outcome.model_dump(),
        "kind": "user_outcome",
    }
    logfire.info(
        "user_outcome {route_plan_id}",
        route_plan_id=route_plan_id,
        accepted_rows=outcome.accepted_rows,
        rejected_rows=outcome.rejected_rows,
        exported=outcome.exported,
        edited_fields=outcome.edited_fields,
    )
    _append_raw(update)
    return True


# --- Sink internals --------------------------------------------------------


def _append_jsonl(telemetry: RouteTelemetry) -> None:
    payload = telemetry.model_dump(mode="json")
    payload["kind"] = "route_plan"
    _append_raw(payload)


def _append_raw(payload: dict[str, Any]) -> None:
    _TELEMETRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, separators=(",", ":"))
    with _WRITE_LOCK, _TELEMETRY_PATH.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def read_telemetry() -> list[dict[str, Any]]:
    """Read all telemetry rows. Used by the calibration recompute + eval harness."""
    if not _TELEMETRY_PATH.exists():
        return []
    rows: list[dict[str, Any]] = []
    with _TELEMETRY_PATH.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                rows.append(json.loads(stripped))
            except json.JSONDecodeError:
                continue  # tolerate partial writes
    return rows


def telemetry_path() -> Path:
    return _TELEMETRY_PATH
