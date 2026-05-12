"use client";

import clsx from "clsx";
import {
  ArrowDownToLine,
  BadgeCheck,
  CircleDollarSign,
  FileSpreadsheet,
  Gauge,
  Layers3,
  Loader2,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  Upload,
} from "lucide-react";
import type { ChangeEvent, KeyboardEvent, ReactNode } from "react";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  downloadBlob,
  exportResults,
  getProviders,
  previewSpreadsheet,
  runResearch,
} from "@/lib/api";
import { compactNumber, currency, displayValue, normalizeField, percent } from "@/lib/format";
import type {
  CellValue,
  InputRow,
  ProviderId,
  ProviderPublic,
  ResearchPayload,
  ResearchResponse,
  ResultRow,
  RoutingMode,
} from "@/types/research";

const DEFAULT_FIELDS = [
  "firm",
  "role",
  "sector_focus",
  "geography",
  "email_status",
  "linkedin_profile",
  "recent_signal",
  "source_notes",
];

const FALLBACK_PROVIDERS: ProviderPublic[] = [
  {
    id: "parallel",
    label: "Parallel",
    env_keys: ["PARALLEL_API_KEY"],
    strengths: ["cited research", "structured enrichment", "source basis"],
    estimated_search_cost: 0.006,
    estimated_row_cost: 0.035,
    speed_score: 0.78,
    quality_score: 0.94,
    coverage_score: 0.91,
    available: false,
  },
  {
    id: "brave",
    label: "Brave",
    env_keys: ["BRAVE_API_KEY"],
    strengths: ["fresh web index", "low cost", "fast retrieval"],
    estimated_search_cost: 0.003,
    estimated_row_cost: 0.018,
    speed_score: 0.92,
    quality_score: 0.76,
    coverage_score: 0.82,
    available: false,
  },
  {
    id: "exa",
    label: "Exa",
    env_keys: ["EXA_API_KEY"],
    strengths: ["semantic search", "company context", "long excerpts"],
    estimated_search_cost: 0.008,
    estimated_row_cost: 0.028,
    speed_score: 0.72,
    quality_score: 0.88,
    coverage_score: 0.84,
    available: false,
  },
  {
    id: "tavily",
    label: "Tavily",
    env_keys: ["TAVILY_API_KEY"],
    strengths: ["general research", "balanced cost", "quick runs"],
    estimated_search_cost: 0.004,
    estimated_row_cost: 0.021,
    speed_score: 0.86,
    quality_score: 0.8,
    coverage_score: 0.8,
    available: false,
  },
  {
    id: "perplexity",
    label: "Perplexity",
    env_keys: ["PERPLEXITY_API_KEY"],
    strengths: ["answer briefs", "citations", "synthesis"],
    estimated_search_cost: 0.005,
    estimated_row_cost: 0.032,
    speed_score: 0.8,
    quality_score: 0.86,
    coverage_score: 0.78,
    available: false,
  },
];

type RunState = "idle" | "reading" | "running" | "exporting";

export function Workspace() {
  const [providers, setProviders] = useState<ProviderPublic[]>(FALLBACK_PROVIDERS);
  const [query, setQuery] = useState(
    "Find US and European LP contacts for lower-mid-market healthcare funds, including role, geography, and recent public signals.",
  );
  const [rows, setRows] = useState<InputRow[]>([]);
  const [columns, setColumns] = useState<string[]>([]);
  const [fields, setFields] = useState<string[]>(DEFAULT_FIELDS);
  const [customField, setCustomField] = useState("");
  const [routingMode, setRoutingMode] = useState<RoutingMode>("best");
  const [manualProvider, setManualProvider] = useState<ProviderId>("parallel");
  const [result, setResult] = useState<ResearchResponse | null>(null);
  const [status, setStatus] = useState<RunState>("idle");
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    getProviders()
      .then((nextProviders) => {
        setProviders(nextProviders);
        if (nextProviders.length) {
          setManualProvider(nextProviders[0].id);
        }
      })
      .catch((providerError: Error) => {
        setError(providerError.message);
      });
  }, []);

  const activeProvider = useMemo(
    () => providers.find((provider) => provider.id === manualProvider) ?? providers[0],
    [manualProvider, providers],
  );

  const previewRows = rows.slice(0, 6);
  const previewColumns = columns.slice(0, 5);
  const connectedProviders = providers.filter((provider) => provider.available).length;
  const busy = status !== "idle";
  const resultColumns = result?.columns ?? [];
  const resultRows = result?.rows ?? [];

  async function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setStatus("reading");
    setError(null);
    try {
      const preview = await previewSpreadsheet(file);
      setRows(preview.rows);
      setColumns(preview.columns);
      setResult(null);
    } catch (previewError) {
      setError(errorMessage(previewError));
    } finally {
      setStatus("idle");
    }
  }

  function toggleField(field: string) {
    setFields((current) =>
      current.includes(field) ? current.filter((item) => item !== field) : [...current, field],
    );
  }

  function addField() {
    const field = normalizeField(customField);
    if (!field) return;
    setFields((current) => (current.includes(field) ? current : [...current, field]));
    setCustomField("");
  }

  function handleCustomFieldKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") {
      event.preventDefault();
      addField();
    }
  }

  async function handleRun() {
    if (!query.trim() && rows.length === 0) {
      setError("Add a search brief or upload a spreadsheet before running.");
      return;
    }
    setStatus("running");
    setError(null);
    try {
      const payload: ResearchPayload = {
        mode: rows.length ? "enrich" : "search",
        query: query.trim(),
        rows,
        fields,
        routing_mode: routingMode,
        provider: routingMode === "manual" ? manualProvider : null,
        max_results: 8,
      };
      setResult(await runResearch(payload));
    } catch (runError) {
      setError(errorMessage(runError));
    } finally {
      setStatus("idle");
    }
  }

  async function handleExport(kind: "csv" | "pdf") {
    if (!result) return;
    setStatus("exporting");
    setError(null);
    try {
      const blob = await exportResults(kind, result);
      downloadBlob(blob, `ct-search-results.${kind}`);
    } catch (exportError) {
      setError(errorMessage(exportError));
    } finally {
      setStatus("idle");
    }
  }

  return (
    <div className="workspace-shell">
      <aside className="system-rail" aria-label="Workspace status">
        <div className="brand-block">
          <span className="overline">ControlThrive</span>
          <h1>CT Search</h1>
          <p>Capital formation research, routed with cost and confidence in view.</p>
        </div>

        <div className="rail-group">
          <span className="rail-label">Provider Status</span>
          <div className="provider-stack">
            {providers.map((provider) => (
              <div
                className={clsx("provider-status", provider.available && "is-live")}
                key={provider.id}
              >
                <span>{provider.label}</span>
                <strong>{provider.available ? "Live" : provider.env_keys[0]}</strong>
              </div>
            ))}
          </div>
        </div>

        <div className="route-brief">
          <span className="rail-label">Current Route</span>
          <strong>{result?.provider_label ?? activeProvider?.label ?? "Auto"}</strong>
          <p>{result?.route.reason ?? "Run a job to price and select the provider."}</p>
          <span>{result ? currency(result.estimated_cost) : "$0.0000"} estimated</span>
        </div>
      </aside>

      <main className="operator-grid">
        <section className="input-panel" aria-labelledby="input-heading">
          <PanelHeader
            icon={<Search aria-hidden="true" size={18} />}
            eyebrow="Input"
            title="Search or enrich"
          />

          <label className="field-label" htmlFor="query">
            Brief
          </label>
          <textarea
            id="query"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            rows={8}
            spellCheck
          />

          <button
            className="upload-target"
            type="button"
            onClick={() => fileInputRef.current?.click()}
          >
            <FileSpreadsheet aria-hidden="true" size={24} />
            <span>
              <strong>{rows.length ? `${compactNumber(rows.length)} rows loaded` : "Spreadsheet"}</strong>
              <small>{columns.length ? columns.join(" / ") : "CSV or Excel contact lists"}</small>
            </span>
            <Upload aria-hidden="true" size={18} />
          </button>
          <input
            ref={fileInputRef}
            className="sr-only"
            type="file"
            accept=".csv,.xlsx,.xls,.xlsm,.txt"
            onChange={handleFileChange}
          />

          <div className="field-toolbar">
            <div>
              <span className="rail-label">Fields</span>
              <strong>{fields.length} selected</strong>
            </div>
            <button type="button" onClick={() => setFields([])}>
              Clear
            </button>
          </div>

          <div className="chip-grid" aria-label="Enrichment fields">
            {DEFAULT_FIELDS.map((field) => (
              <button
                className={clsx("field-chip", fields.includes(field) && "is-active")}
                key={field}
                type="button"
                onClick={() => toggleField(field)}
              >
                {field}
              </button>
            ))}
            {fields
              .filter((field) => !DEFAULT_FIELDS.includes(field))
              .map((field) => (
                <button
                  className="field-chip is-active"
                  key={field}
                  type="button"
                  onClick={() => toggleField(field)}
                >
                  {field}
                </button>
              ))}
          </div>

          <div className="custom-field-row">
            <input
              type="text"
              value={customField}
              onChange={(event) => setCustomField(event.target.value)}
              onKeyDown={handleCustomFieldKeyDown}
              placeholder="custom_field"
              aria-label="Custom enrichment field"
            />
            <button type="button" onClick={addField}>
              Add
            </button>
          </div>
        </section>

        <section className="routing-panel" aria-labelledby="routing-heading">
          <PanelHeader
            icon={<SlidersHorizontal aria-hidden="true" size={18} />}
            eyebrow="Routing"
            title="Choose the edge"
          />

          <div className="route-tabs" role="group" aria-label="Routing preference">
            {(["best", "cost", "speed", "confidence"] satisfies RoutingMode[]).map((mode) => (
              <button
                className={clsx(routingMode === mode && "is-active")}
                key={mode}
                type="button"
                onClick={() => setRoutingMode(mode)}
              >
                {mode}
              </button>
            ))}
          </div>

          <div className="provider-list" aria-label="Providers">
            {providers.map((provider) => (
              <ProviderTile
                key={provider.id}
                provider={provider}
                active={routingMode === "manual" && manualProvider === provider.id}
                onSelect={() => {
                  setManualProvider(provider.id);
                  setRoutingMode("manual");
                }}
              />
            ))}
          </div>

          <div className="run-bar">
            <button className="run-button" type="button" onClick={handleRun} disabled={busy}>
              {status === "running" ? (
                <Loader2 className="spin" aria-hidden="true" size={18} />
              ) : (
                <Layers3 aria-hidden="true" size={18} />
              )}
              Run research
            </button>
            <span>{statusLabel(status)}</span>
          </div>

          <div className="preview-panel">
            <div className="table-heading">
              <span className="rail-label">Preview</span>
              <strong>{compactNumber(rows.length)} rows</strong>
            </div>
            {previewRows.length ? (
              <DataTable
                columns={previewColumns}
                rows={previewRows.map((row) => previewColumns.map((column) => row[column]))}
                compact
              />
            ) : (
              <EmptyBlock title="No list loaded" body="Search mode is active." />
            )}
          </div>
        </section>

        <section className="results-panel" aria-labelledby="results-heading">
          <div className="results-header">
            <PanelHeader
              icon={<BadgeCheck aria-hidden="true" size={18} />}
              eyebrow="Output"
              title="Cited results"
            />
            <div className="export-group">
              <button
                type="button"
                onClick={() => handleExport("csv")}
                disabled={!result || busy}
                aria-label="Export CSV"
              >
                <ArrowDownToLine aria-hidden="true" size={16} />
                CSV
              </button>
              <button
                type="button"
                onClick={() => handleExport("pdf")}
                disabled={!result || busy}
                aria-label="Export PDF"
              >
                <ArrowDownToLine aria-hidden="true" size={16} />
                PDF
              </button>
            </div>
          </div>

          <div className="metric-strip" aria-label="Run metrics">
            <Metric label="Rows" value={compactNumber(resultRows.length)} icon={<Layers3 size={15} />} />
            <Metric
              label="Cost"
              value={result ? currency(result.estimated_cost) : "$0.0000"}
              icon={<CircleDollarSign size={15} />}
            />
            <Metric
              label="Mode"
              value={result?.is_demo ? "Demo" : result ? "Live" : "Ready"}
              icon={<ShieldCheck size={15} />}
            />
            <Metric
              label="Latency"
              value={result ? `${result.elapsed_ms} ms` : "—"}
              icon={<Gauge size={15} />}
            />
          </div>

          {error ? <div className="error-banner">{error}</div> : null}
          {result?.warnings.length ? (
            <div className="warning-stack">
              {result.warnings.map((warning) => (
                <span key={warning}>{warning}</span>
              ))}
            </div>
          ) : null}

          <div className="results-table-wrap">
            {result ? (
              <ResultsTable columns={resultColumns} rows={resultRows} />
            ) : (
              <EmptyBlock
                title={`${connectedProviders} live providers`}
                body="Results will appear with provider, confidence, and citations."
              />
            )}
          </div>
        </section>
      </main>
    </div>
  );
}

function PanelHeader({
  eyebrow,
  icon,
  title,
}: {
  eyebrow: string;
  icon: ReactNode;
  title: string;
}) {
  return (
    <div className="panel-header">
      <span>{icon}</span>
      <div>
        <p>{eyebrow}</p>
        <h2>{title}</h2>
      </div>
    </div>
  );
}

function ProviderTile({
  active,
  onSelect,
  provider,
}: {
  active: boolean;
  onSelect: () => void;
  provider: ProviderPublic;
}) {
  return (
    <button className={clsx("provider-tile", active && "is-active")} type="button" onClick={onSelect}>
      <span className="provider-title">
        <strong>{provider.label}</strong>
        <em>{provider.available ? "Live" : "Demo"}</em>
      </span>
      <small>{provider.strengths.join(" / ")}</small>
      <span className="provider-metrics">
        <span>{currency(provider.estimated_search_cost)}</span>
        <span>{percent(provider.speed_score)} speed</span>
        <span>{percent(provider.quality_score)} confidence</span>
      </span>
    </button>
  );
}

function Metric({ icon, label, value }: { icon: ReactNode; label: string; value: string }) {
  return (
    <div className="metric">
      <span>{icon}</span>
      <small>{label}</small>
      <strong>{value}</strong>
    </div>
  );
}

function ResultsTable({ columns, rows }: { columns: string[]; rows: ResultRow[] }) {
  return (
    <DataTable
      columns={[...columns, "confidence", "provider", "citations"]}
      rows={rows.map((row) => {
        const combined: Record<string, CellValue> = { ...row.input, ...row.fields };
        return [
          ...columns.map((column) => combined[column] ?? null),
          row.confidence,
          row.provider,
          row.citations,
        ];
      })}
    />
  );
}

function DataTable({
  columns,
  compact = false,
  rows,
}: {
  columns: string[];
  compact?: boolean;
  rows: Array<Array<CellValue | ResultRow["citations"] | undefined>>;
}) {
  return (
    <table className={clsx("data-table", compact && "is-compact")}>
      <thead>
        <tr>
          {columns.map((column) => (
            <th data-column={column} key={column}>
              {column}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, rowIndex) => (
          <tr key={`row-${rowIndex}`}>
            {row.map((cell, cellIndex) => (
              <td data-column={columns[cellIndex]} key={`${rowIndex}-${columns[cellIndex]}`}>
                {renderCell(columns[cellIndex], cell)}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function EmptyBlock({ body, title }: { body: string; title: string }) {
  return (
    <div className="empty-block">
      <strong>{title}</strong>
      <span>{body}</span>
    </div>
  );
}

function renderCell(column: string, cell: CellValue | ResultRow["citations"] | undefined) {
  if (Array.isArray(cell)) {
    if (!cell.length) return <span className="muted-cell">—</span>;
    return (
      <div className="citation-stack">
        {cell.slice(0, 3).map((citation) =>
          citation.url ? (
            <a key={citation.url} href={citation.url} target="_blank" rel="noreferrer">
              {citation.title || "source"}
            </a>
          ) : (
            <span key={citation.title}>{citation.title || citation.excerpt || "source"}</span>
          ),
        )}
      </div>
    );
  }
  if (column === "url" && typeof cell === "string" && cell) {
    return (
      <a href={cell} target="_blank" rel="noreferrer">
        open
      </a>
    );
  }
  if (column === "confidence" && typeof cell === "number") {
    return <span className="confidence">{percent(cell)}</span>;
  }
  if (column === "summary") {
    return <span className="summary-cell">{displayValue(cell)}</span>;
  }
  return <span>{displayValue(cell)}</span>;
}

function statusLabel(status: RunState) {
  if (status === "reading") return "Reading file";
  if (status === "running") return "Researching";
  if (status === "exporting") return "Exporting";
  return "Idle";
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "Something went wrong.";
}
