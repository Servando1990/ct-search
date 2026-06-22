from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

import logfire
import pandas as pd
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from ct_search import store
from ct_search.models import ExportRequest, ResearchRequest, RunDetail, RunSummary
from ct_search.providers import public_providers, run_research
from ct_search.resolve import dedupe
from ct_search.runs import TERMINAL_EVENT_KINDS, get_run_manager
from ct_search.settings import get_settings
from ct_search.telemetry import (
    UserOutcome,
    configure_logfire,
    record_dedupe_decision,
    record_user_outcome,
)

# Logfire — configure once, before FastAPI auto-instrumentation hooks.
configure_logfire()


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Runs are in-process asyncio tasks; anything still open from a previous
    # process can never finish, so mark it errored on boot.
    orphaned = store.fail_orphaned_runs("Server restarted before the run finished.")
    if orphaned:
        logfire.info("orphaned_runs_failed {count}", count=orphaned)
    yield


app = FastAPI(title="Edna Search", version="0.1.0", lifespan=_lifespan)
logfire.instrument_fastapi(app, capture_headers=False)
logfire.instrument_httpx()
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def index() -> dict[str, str]:
    return {"name": "Edna Search API", "status": "ready"}


@app.get("/api/providers")
async def providers() -> list[dict[str, Any]]:
    settings = get_settings()
    return [provider.model_dump() for provider in public_providers(settings)]


@app.post("/api/preview")
async def preview_spreadsheet(file: Annotated[UploadFile, File(...)]) -> dict[str, Any]:
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    frame = _read_spreadsheet(content, file.filename or "")
    rows = _frame_to_records(frame.head(200))
    return {
        "filename": file.filename,
        "row_count": int(len(frame)),
        "columns": [str(column) for column in frame.columns],
        "rows": rows,
    }


@app.post("/api/dedupe")
async def dedupe_preview(payload: dict[str, Any]) -> dict[str, Any]:
    """Cluster uploaded rows that look like the same entity (upload-preview banner).

    Suggestions only — merges are operator-confirmed via /api/dedupe/decision
    and never applied here (docs/match-spec.md §1.2, §2.1).
    """
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise HTTPException(status_code=400, detail="Provide a `rows` list to dedupe.")
    clusters = dedupe([row for row in rows if isinstance(row, dict)])
    return {
        "rows_hash": _rows_hash(rows),
        "cluster_count": len(clusters),
        "clusters": [cluster.model_dump() for cluster in clusters],
    }


@app.post("/api/dedupe/decision")
async def dedupe_decision(payload: dict[str, Any]) -> dict[str, Any]:
    """Record an operator merge / keep-separate decision for calibration."""
    rows_hash = payload.get("rows_hash")
    row_indices = payload.get("row_indices")
    decision = payload.get("decision")
    if not isinstance(rows_hash, str) or not isinstance(row_indices, list):
        raise HTTPException(status_code=400, detail="rows_hash and row_indices are required.")
    if decision not in ("merged", "separate"):
        raise HTTPException(status_code=400, detail="decision must be 'merged' or 'separate'.")
    record_dedupe_decision(
        rows_hash=rows_hash,
        row_indices=[int(index) for index in row_indices],
        decision=decision,
        basis=str(payload.get("basis") or ""),
    )
    return {"recorded": True}


@app.post("/api/research")
async def research(request: ResearchRequest):
    """Synchronous run — kept for back-compat and scripting; the workbench
    uses the async /api/runs flow below."""
    if not request.query and not request.rows:
        raise HTTPException(status_code=400, detail="Provide a search query or upload rows.")
    settings = get_settings()
    return await run_research(request, settings)


# --- Async runs (phase 2) ----------------------------------------------------


@app.post("/api/runs")
async def create_run(request: ResearchRequest) -> dict[str, str]:
    if not request.query and not request.rows:
        raise HTTPException(status_code=400, detail="Provide a search query or upload rows.")
    run_id = get_run_manager().start_run(request, get_settings())
    return {"run_id": run_id, "status": "queued"}


@app.get("/api/runs")
async def list_runs(limit: int = 12) -> list[RunSummary]:
    return [RunSummary.model_validate(run) for run in store.list_runs(min(max(limit, 1), 50))]


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str) -> RunDetail:
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    return RunDetail.model_validate(run)


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str) -> StreamingResponse:
    """SSE progress stream: replays persisted events, then tails live ones."""
    if store.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    return StreamingResponse(
        _event_stream(run_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _event_stream(run_id: str) -> AsyncIterator[str]:
    manager = get_run_manager()
    # Subscribe before replaying so nothing falls between the two.
    queue = manager.subscribe(run_id)
    try:
        last_seq = 0
        for event in store.list_events(run_id):
            last_seq = event["seq"]
            yield _sse(event)
            if event["kind"] in TERMINAL_EVENT_KINDS:
                return

        run = store.get_run(run_id)
        if run and run["status"] in ("done", "error"):
            return

        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15.0)
            except TimeoutError:
                yield ": ping\n\n"
                run = store.get_run(run_id)
                if run and run["status"] in ("done", "error"):
                    return
                continue
            if event is None:  # run closed
                return
            if event["seq"] <= last_seq:
                continue  # already replayed
            last_seq = event["seq"]
            yield _sse(event)
            if event["kind"] in TERMINAL_EVENT_KINDS:
                return
    finally:
        manager.unsubscribe(run_id, queue)


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, separators=(',', ':'))}\n\n"


@app.post("/api/telemetry/outcome")
async def telemetry_outcome(payload: dict[str, Any]) -> dict[str, Any]:
    """Attach user accept/reject/export signals to a previously-routed plan.

    Frontend calls this after the user reviews results; the recompute job
    uses the outcomes to update vendor priors. See docs/decision-framework.md
    §"Calibration loop".
    """
    route_plan_id = payload.get("route_plan_id")
    if not route_plan_id or not isinstance(route_plan_id, str):
        raise HTTPException(status_code=400, detail="route_plan_id is required.")
    outcome = UserOutcome.model_validate(
        {k: v for k, v in payload.items() if k != "route_plan_id"}
    )
    record_user_outcome(route_plan_id, outcome)
    return {"recorded": True, "route_plan_id": route_plan_id}


@app.post("/api/export/csv")
async def export_csv(request: ExportRequest) -> StreamingResponse:
    buffer = io.StringIO()
    columns = _export_columns(request)
    writer = csv.DictWriter(buffer, fieldnames=columns)
    writer.writeheader()
    for row in request.rows:
        writer.writerow(_flatten_row(row.model_dump(), columns))
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="edna-search-results.csv"'},
    )


@app.post("/api/export/pdf")
async def export_pdf(request: ExportRequest) -> StreamingResponse:
    buffer = io.BytesIO()
    columns = _export_columns(request)[:8]
    document = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=0.45 * inch,
        leftMargin=0.45 * inch,
        topMargin=0.55 * inch,
        bottomMargin=0.55 * inch,
    )
    styles = getSampleStyleSheet()
    story: list[Any] = []
    story.append(Paragraph(request.title or "Edna Search Results", styles["Title"]))
    if request.route:
        story.append(
            Paragraph(
                f"Provider: {request.route.label} | Routing: {request.route.routing_mode} | "
                f"Estimated cost: ${request.route.estimated_cost:.4f}",
                styles["Normal"],
            )
        )
    story.append(Spacer(1, 0.18 * inch))

    table_data = [columns]
    for result_row in request.rows[:40]:
        flattened = _flatten_row(result_row.model_dump(), columns)
        table_data.append([_pdf_cell(flattened.get(column, "")) for column in columns])

    table = Table(table_data, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#16251f")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#f5f1e7")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d8d2c4")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8f6ef")]),
            ]
        )
    )
    story.append(table)
    document.build(story)
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="edna-search-results.pdf"'},
    )


def _rows_hash(rows: list[Any]) -> str:
    """Stable id for an uploaded row set so dedupe decisions can be joined later."""
    serialized = json.dumps(rows, sort_keys=True, default=str)
    return hashlib.sha1(serialized.encode("utf-8")).hexdigest()[:16]


def _read_spreadsheet(content: bytes, filename: str) -> pd.DataFrame:
    suffix = Path(filename).suffix.lower()
    stream = io.BytesIO(content)
    try:
        if suffix in {".xlsx", ".xlsm", ".xls"}:
            frame = pd.read_excel(stream)
        elif suffix in {".csv", ".txt"}:
            frame = pd.read_csv(stream)
        else:
            raise HTTPException(status_code=400, detail="Upload a CSV or Excel spreadsheet.")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read spreadsheet: {exc}") from exc

    frame = frame.dropna(how="all")
    frame.columns = [
        str(column).strip() or f"Column {index + 1}"
        for index, column in enumerate(frame.columns)
    ]
    return frame


def _frame_to_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    cleaned = frame.where(pd.notnull(frame), "")
    records: list[dict[str, Any]] = []
    for record in cleaned.to_dict(orient="records"):
        records.append({str(key): _json_safe(value) for key, value in record.items()})
    return records


def _json_safe(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _export_columns(request: ExportRequest) -> list[str]:
    columns: list[str] = []
    for column in request.columns:
        if column not in columns:
            columns.append(column)
    for row in request.rows:
        for key in [*row.input.keys(), *row.fields.keys()]:
            if key not in columns:
                columns.append(key)
    for metadata in ("confidence", "via", "verified", "citations"):
        if metadata not in columns:
            columns.append(metadata)
    return columns


def _flatten_row(row: dict[str, Any], columns: list[str]) -> dict[str, Any]:
    input_values = row.get("input") or {}
    field_values = row.get("fields") or {}
    citations = row.get("citations") or []
    contributing = row.get("contributing_providers") or []
    flattened = {**input_values, **field_values}
    flattened["confidence"] = row.get("confidence", "")
    flattened["provider"] = row.get("provider", "")
    flattened["step_role"] = row.get("step_role", "")
    flattened["verified"] = "yes" if row.get("verified") else ""
    flattened["via"] = " + ".join(contributing) if contributing else row.get("provider", "")
    flattened["citations"] = " | ".join(
        citation.get("url", "") for citation in citations if citation.get("url")
    )
    return {column: flattened.get(column, "") for column in columns}


def _pdf_cell(value: Any) -> str:
    text = str(value or "")
    return text if len(text) <= 160 else text[:157] + "..."
