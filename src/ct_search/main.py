from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Annotated, Any

import pandas as pd
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from ct_search.models import ExportRequest, ResearchRequest
from ct_search.providers import public_providers, run_research
from ct_search.settings import get_settings

app = FastAPI(title="CT Search", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:3000", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def index() -> dict[str, str]:
    return {"name": "CT Search API", "status": "ready"}


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


@app.post("/api/research")
async def research(request: ResearchRequest):
    if not request.query and not request.rows:
        raise HTTPException(status_code=400, detail="Provide a search query or upload rows.")
    settings = get_settings()
    return await run_research(request, settings)


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
        headers={"Content-Disposition": 'attachment; filename="ct-search-results.csv"'},
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
    story.append(Paragraph(request.title or "CT Search Results", styles["Title"]))
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
        headers={"Content-Disposition": 'attachment; filename="ct-search-results.pdf"'},
    )


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
    for metadata in ("confidence", "provider", "citations"):
        if metadata not in columns:
            columns.append(metadata)
    return columns


def _flatten_row(row: dict[str, Any], columns: list[str]) -> dict[str, Any]:
    input_values = row.get("input") or {}
    field_values = row.get("fields") or {}
    citations = row.get("citations") or []
    flattened = {**input_values, **field_values}
    flattened["confidence"] = row.get("confidence", "")
    flattened["provider"] = row.get("provider", "")
    flattened["citations"] = " | ".join(
        citation.get("url", "") for citation in citations if citation.get("url")
    )
    return {column: flattened.get(column, "") for column in columns}


def _pdf_cell(value: Any) -> str:
    text = str(value or "")
    return text if len(text) <= 160 else text[:157] + "..."
