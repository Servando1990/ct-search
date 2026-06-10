"use client";

import clsx from "clsx";
import {
  ArrowDownToLine,
  ArrowUpRight,
  Check,
  FileSpreadsheet,
  Loader2,
  Plus,
  RotateCcw,
  SlidersHorizontal,
  X,
} from "lucide-react";
import Link from "next/link";
import type { ChangeEvent, KeyboardEvent } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  downloadBlob,
  exportResults,
  getProviders,
  postOutcome,
  previewSpreadsheet,
  runResearch,
} from "@/lib/api";
import { compactNumber, currency, displayValue, normalizeField, percent } from "@/lib/format";
import type {
  CellValue,
  EvidenceRisk,
  InputRow,
  ProviderId,
  ProviderPublic,
  ResearchPayload,
  ResearchResponse,
  ResultRow,
  RouteDecision,
  RouteStep,
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

const METADATA_COLUMNS = ["confidence", "via", "citations"] as const;

const EXAMPLE_BRIEFS = [
  "Map LPs that have backed lower-middle-market healthcare funds since 2024",
  "Find IR contacts at single-family offices active in European industrials",
  "Brief me on independent sponsor activity in industrial services this year",
];

const FALLBACK_PROVIDERS: ProviderPublic[] = [
  {
    id: "parallel",
    label: "Parallel",
    env_keys: ["PARALLEL_API_KEY"],
    strengths: ["cited research", "structured enrichment", "source basis"],
    estimated_search_cost: 0.005,
    estimated_row_cost: 0.025,
    speed_score: 0.78,
    quality_score: 0.94,
    coverage_score: 0.91,
    available: false,
    best_for: ["cited structured enrichment", "multi-hop research"],
    tradeoffs: ["higher-cost processors for deep research"],
    avg_tokens_per_result: 918,
    avg_match_rate: 0.72,
    metrics: [],
  },
  {
    id: "brave",
    label: "Brave",
    env_keys: ["BRAVE_API_KEY"],
    strengths: ["fresh web index", "low cost", "fast retrieval"],
    estimated_search_cost: 0.005,
    estimated_row_cost: 0.024,
    speed_score: 0.92,
    quality_score: 0.76,
    coverage_score: 0.82,
    available: false,
    best_for: ["fast raw web retrieval", "fresh broad web coverage"],
    tradeoffs: ["not a full enrichment workflow by itself"],
    avg_tokens_per_result: 1100,
    avg_match_rate: 0.6,
    metrics: [],
  },
  {
    id: "exa",
    label: "Exa",
    env_keys: ["EXA_API_KEY"],
    strengths: ["semantic search", "company context", "long excerpts"],
    estimated_search_cost: 0.007,
    estimated_row_cost: 0.025,
    speed_score: 0.72,
    quality_score: 0.88,
    coverage_score: 0.84,
    available: false,
    best_for: ["semantic discovery", "company and people search"],
    tradeoffs: ["search pricing is higher than simple retrieval"],
    avg_tokens_per_result: 1300,
    avg_match_rate: 0.65,
    metrics: [],
  },
  {
    id: "tavily",
    label: "Tavily",
    env_keys: ["TAVILY_API_KEY"],
    strengths: ["general research", "balanced cost", "quick runs"],
    estimated_search_cost: 0.008,
    estimated_row_cost: 0.024,
    speed_score: 0.86,
    quality_score: 0.8,
    coverage_score: 0.8,
    available: false,
    best_for: ["balanced agent search", "content extraction"],
    tradeoffs: ["credit costs vary by depth"],
    avg_tokens_per_result: 1928,
    avg_match_rate: 0.62,
    metrics: [],
  },
  {
    id: "perplexity",
    label: "Perplexity",
    env_keys: ["PERPLEXITY_API_KEY"],
    strengths: ["answer briefs", "citations", "synthesis"],
    estimated_search_cost: 0.006,
    estimated_row_cost: 0.032,
    speed_score: 0.8,
    quality_score: 0.86,
    coverage_score: 0.78,
    available: false,
    best_for: ["web-grounded answer synthesis", "citation-backed summaries"],
    tradeoffs: ["Sonar costs include request and token costs"],
    avg_tokens_per_result: 1400,
    avg_match_rate: 0.62,
    metrics: [],
  },
];

type Phase = "compose" | "running" | "result";
type RoutePreference = "auto" | "cost" | "speed" | "confidence";
type VenueChoice = ProviderId | "auto";

export function Workspace() {
  const [providers, setProviders] = useState<ProviderPublic[]>(FALLBACK_PROVIDERS);
  const [phase, setPhase] = useState<Phase>("compose");
  const [query, setQuery] = useState("");
  const [rows, setRows] = useState<InputRow[]>([]);
  const [columns, setColumns] = useState<string[]>([]);
  const [fileName, setFileName] = useState("");
  const [rowCount, setRowCount] = useState(0);
  const [reading, setReading] = useState(false);
  const [exporting, setExporting] = useState(false);

  const [fields, setFields] = useState<string[]>(DEFAULT_FIELDS);
  const [customField, setCustomField] = useState("");
  const [preference, setPreference] = useState<RoutePreference>("auto");
  const [evidenceRisk, setEvidenceRisk] = useState<EvidenceRisk>("medium");
  const [venue, setVenue] = useState<VenueChoice>("auto");
  const [tuneOpen, setTuneOpen] = useState(false);

  const [result, setResult] = useState<ResearchResponse | null>(null);
  const [dropped, setDropped] = useState<ReadonlySet<number>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);

  const briefRef = useRef<HTMLTextAreaElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const outcomeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reviewed = useRef(false);

  useEffect(() => {
    getProviders()
      .then(setProviders)
      .catch(() => {
        /* fall back to static venue list */
      });
  }, []);

  // Auto-grow the brief field with its content.
  useEffect(() => {
    const node = briefRef.current;
    if (!node) return;
    node.style.height = "auto";
    node.style.height = `${node.scrollHeight}px`;
  }, [query, phase]);

  // Run clock while routing.
  useEffect(() => {
    if (phase !== "running") return;
    const started = performance.now();
    const tick = setInterval(() => setElapsed(performance.now() - started), 100);
    return () => clearInterval(tick);
  }, [phase]);

  // Review signals feed the calibration loop, debounced so a burst of
  // keep/drop clicks lands as one outcome row.
  useEffect(() => {
    if (!result?.route_plan_id || !reviewed.current) return;
    if (outcomeTimer.current) clearTimeout(outcomeTimer.current);
    const total = result.rows.length;
    outcomeTimer.current = setTimeout(() => {
      void postOutcome(result.route_plan_id, {
        accepted_rows: total - dropped.size,
        rejected_rows: dropped.size,
        exported: false,
      });
    }, 1200);
    return () => {
      if (outcomeTimer.current) clearTimeout(outcomeTimer.current);
    };
  }, [dropped, result]);

  const liveCount = providers.filter((provider) => provider.available).length;
  const tuned =
    preference !== "auto" ||
    venue !== "auto" ||
    evidenceRisk !== "medium" ||
    fields.join() !== DEFAULT_FIELDS.join();
  const canRun = Boolean(query.trim()) || rows.length > 0;
  const normalizedCustomField = normalizeField(customField);
  const canAddCustomField =
    Boolean(normalizedCustomField) && !fields.includes(normalizedCustomField);
  const venueLabel = useMemo(
    () => providers.find((provider) => provider.id === venue)?.label,
    [providers, venue],
  );

  async function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setReading(true);
    setError(null);
    try {
      const preview = await previewSpreadsheet(file);
      setRows(preview.rows);
      setColumns(preview.columns);
      setRowCount(preview.row_count);
      setFileName(preview.filename || file.name);
    } catch (previewError) {
      setError(errorMessage(previewError));
    } finally {
      event.target.value = "";
      setReading(false);
    }
  }

  function detachFile() {
    setRows([]);
    setColumns([]);
    setRowCount(0);
    setFileName("");
  }

  const handleRun = useCallback(async () => {
    if (!query.trim() && rows.length === 0) {
      setError("Write a brief or attach a list first.");
      return;
    }
    setPhase("running");
    setError(null);
    setElapsed(0);
    reviewed.current = false;
    try {
      const payload: ResearchPayload = {
        mode: rows.length ? "enrich" : "search",
        query: query.trim(),
        rows,
        fields,
        routing_mode: venue !== "auto" ? "manual" : preference === "auto" ? "best" : preference,
        provider: venue !== "auto" ? venue : null,
        max_results: 8,
        evidence_risk: evidenceRisk,
      };
      const response = await runResearch(payload);
      setResult(response);
      setDropped(new Set());
      setPhase("result");
    } catch (runError) {
      setError(errorMessage(runError));
      setPhase("compose");
    }
  }, [evidenceRisk, fields, preference, query, rows, venue]);

  function handleBriefKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      void handleRun();
    }
  }

  function toggleDrop(index: number) {
    reviewed.current = true;
    setDropped((current) => {
      const next = new Set(current);
      if (next.has(index)) {
        next.delete(index);
      } else {
        next.add(index);
      }
      return next;
    });
  }

  async function handleExport(kind: "csv" | "pdf") {
    if (!result) return;
    setExporting(true);
    setError(null);
    try {
      const keptRows = result.rows.filter((_, index) => !dropped.has(index));
      const blob = await exportResults(kind, { ...result, rows: keptRows });
      downloadBlob(blob, `edna-search-results.${kind}`);
      void postOutcome(result.route_plan_id, {
        accepted_rows: keptRows.length,
        rejected_rows: dropped.size,
        exported: true,
      });
    } catch (exportError) {
      setError(errorMessage(exportError));
    } finally {
      setExporting(false);
    }
  }

  function editBrief() {
    setPhase("compose");
    setResult(null);
  }

  function newRun() {
    setPhase("compose");
    setResult(null);
    setQuery("");
    detachFile();
    setError(null);
  }

  function toggleField(field: string) {
    setFields((current) =>
      current.includes(field) ? current.filter((item) => item !== field) : [...current, field],
    );
  }

  function addField() {
    if (!canAddCustomField) return;
    setFields((current) => [...current, normalizedCustomField]);
    setCustomField("");
  }

  function applyExample(brief: string) {
    setQuery(brief);
    briefRef.current?.focus();
  }

  return (
    <div className="desk" data-phase={phase} aria-busy={phase === "running"}>
      <header className="desk-bar">
        <Link className="desk-brand" href="/">
          <span>ControlThrive</span>
          Edna Search
        </Link>
        <div className="desk-bar-right">
          <div className="venue-status" aria-label={`${liveCount} of ${providers.length} venues live`}>
            {providers.map((provider) => (
              <span
                key={provider.id}
                className={clsx("venue-dot", provider.available && "is-live")}
                title={`${provider.label} — ${provider.available ? "live" : "demo until " + provider.env_keys[0] + " is set"}`}
              />
            ))}
            <span className="venue-count">{liveCount}/{providers.length} live</span>
          </div>
        </div>
      </header>

      <main className="desk-main">
        {phase === "compose" ? (
          <section className="compose-stage" aria-labelledby="compose-heading">
            <p className="compose-overline" id="compose-heading">
              New run
            </p>
            <div className="composer">
              <label className="sr-only" htmlFor="brief">
                Research brief
              </label>
              <textarea
                id="brief"
                ref={briefRef}
                className="composer-input"
                value={query}
                rows={3}
                placeholder={
                  rows.length
                    ? "What should Edna add to this list?"
                    : "Brief the desk. e.g. Find LP contacts for healthcare funds raising in the US and Europe…"
                }
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={handleBriefKeyDown}
                spellCheck
                autoFocus
              />
              <div className="composer-foot">
                {fileName ? (
                  <div className="attach-chip">
                    <FileSpreadsheet aria-hidden="true" size={15} />
                    <span className="attach-name">{fileName}</span>
                    <span className="attach-meta">
                      {compactNumber(rowCount)} rows · {columns.length} cols
                    </span>
                    <button type="button" onClick={detachFile} aria-label="Remove attached list">
                      <X aria-hidden="true" size={14} />
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    className="attach-button"
                    disabled={reading}
                    onClick={() => fileInputRef.current?.click()}
                  >
                    {reading ? (
                      <Loader2 className="spin" aria-hidden="true" size={15} />
                    ) : (
                      <FileSpreadsheet aria-hidden="true" size={15} />
                    )}
                    {reading ? "Reading list" : "Attach a list to enrich"}
                    <span className="attach-hint">CSV / XLSX</span>
                  </button>
                )}
                <div className="composer-run">
                  <span className={clsx("route-flag", tuned && "is-tuned")}>
                    {tuned
                      ? ["Tuned", preference !== "auto" ? preference : null, venueLabel]
                          .filter(Boolean)
                          .join(" · ")
                      : "Auto route"}
                  </span>
                  <button
                    type="button"
                    className="run-button"
                    onClick={() => void handleRun()}
                    disabled={!canRun || reading}
                  >
                    Run
                    <kbd>⌘↵</kbd>
                  </button>
                </div>
              </div>
            </div>
            <input
              ref={fileInputRef}
              type="file"
              accept=".csv,.xlsx,.xls,.xlsm,.txt"
              hidden
              onChange={handleFileChange}
            />

            {rows.length ? (
              <p className="returns-line">
                Returns {fields.slice(0, 4).map(formatColumnLabel).join(" · ")}
                {fields.length > 4 ? ` · +${fields.length - 4} more` : ""}
                <button type="button" onClick={() => setTuneOpen(true)}>
                  Edit fields
                </button>
              </p>
            ) : null}

            {error ? (
              <div className="error-banner" role="alert">
                {error}
              </div>
            ) : null}

            <div className="compose-meta">
              <button
                type="button"
                className={clsx("tune-toggle", tuneOpen && "is-open")}
                onClick={() => setTuneOpen((current) => !current)}
                aria-expanded={tuneOpen}
                aria-controls="tune-panel"
              >
                <SlidersHorizontal aria-hidden="true" size={14} />
                Tune route
              </button>
              {tuned && !tuneOpen ? (
                <button
                  type="button"
                  className="tune-reset"
                  onClick={() => {
                    setPreference("auto");
                    setVenue("auto");
                    setEvidenceRisk("medium");
                    setFields(DEFAULT_FIELDS);
                  }}
                >
                  Reset to auto
                </button>
              ) : null}
            </div>

            {tuneOpen ? (
              <div className="tune-panel" id="tune-panel">
                <div className="tune-section">
                  <span className="tune-label">Optimize for</span>
                  <div className="seg" role="group" aria-label="Routing preference">
                    {(["auto", "cost", "speed", "confidence"] satisfies RoutePreference[]).map(
                      (mode) => (
                        <button
                          key={mode}
                          type="button"
                          className={clsx(preference === mode && "is-active")}
                          aria-pressed={preference === mode}
                          onClick={() => setPreference(mode)}
                        >
                          {mode}
                        </button>
                      ),
                    )}
                  </div>
                </div>

                <div className="tune-section">
                  <span className="tune-label">Evidence risk</span>
                  <div className="seg" role="group" aria-label="Evidence risk">
                    {(["low", "medium", "high"] satisfies EvidenceRisk[]).map((risk) => (
                      <button
                        key={risk}
                        type="button"
                        className={clsx(evidenceRisk === risk && "is-active")}
                        aria-pressed={evidenceRisk === risk}
                        onClick={() => setEvidenceRisk(risk)}
                      >
                        {risk}
                      </button>
                    ))}
                  </div>
                  <p className="tune-hint">{evidenceRiskHint(evidenceRisk)}</p>
                </div>

                <div className="tune-section">
                  <span className="tune-label">Venue</span>
                  <div className="venue-list" role="radiogroup" aria-label="Venue override">
                    <button
                      type="button"
                      role="radio"
                      aria-checked={venue === "auto"}
                      className={clsx("venue-option", venue === "auto" && "is-active")}
                      onClick={() => setVenue("auto")}
                    >
                      <strong>Auto</strong>
                      <span className="venue-note">Router scores all venues against the brief</span>
                    </button>
                    {providers.map((provider) => (
                      <button
                        key={provider.id}
                        type="button"
                        role="radio"
                        aria-checked={venue === provider.id}
                        className={clsx("venue-option", venue === provider.id && "is-active")}
                        onClick={() => setVenue(provider.id)}
                      >
                        <strong>{provider.label}</strong>
                        <span className="venue-note">{provider.strengths.join(" · ")}</span>
                        <span className="venue-figures">
                          {currency(provider.estimated_search_cost)} · q {percent(provider.quality_score)}
                          {" · "}
                          <em className={provider.available ? "is-live" : undefined}>
                            {provider.available ? "live" : "demo"}
                          </em>
                        </span>
                      </button>
                    ))}
                  </div>
                </div>

                <div className="tune-section">
                  <span className="tune-label">Returned fields</span>
                  <div className="chip-grid" aria-label="Enrichment fields">
                    {[...DEFAULT_FIELDS, ...fields.filter((field) => !DEFAULT_FIELDS.includes(field))].map(
                      (field) => (
                        <button
                          key={field}
                          type="button"
                          className={clsx("field-chip", fields.includes(field) && "is-active")}
                          aria-pressed={fields.includes(field)}
                          onClick={() => toggleField(field)}
                        >
                          {formatColumnLabel(field)}
                        </button>
                      ),
                    )}
                  </div>
                  <div className="custom-field-row">
                    <input
                      type="text"
                      value={customField}
                      onChange={(event) => setCustomField(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") {
                          event.preventDefault();
                          addField();
                        }
                      }}
                      placeholder="Add a custom field"
                      aria-label="Custom enrichment field"
                    />
                    <button type="button" onClick={addField} disabled={!canAddCustomField}>
                      <Plus aria-hidden="true" size={14} />
                      Add
                    </button>
                  </div>
                </div>
              </div>
            ) : null}

            {!query && !rows.length && !tuneOpen ? (
              <div className="example-list" aria-label="Example briefs">
                {EXAMPLE_BRIEFS.map((brief) => (
                  <button key={brief} type="button" onClick={() => applyExample(brief)}>
                    <ArrowUpRight aria-hidden="true" size={14} />
                    {brief}
                  </button>
                ))}
              </div>
            ) : null}
          </section>
        ) : null}

        {phase === "running" ? (
          <section className="routing-stage" role="status" aria-live="polite">
            <p className="brief-echo">{query.trim() || fileName}</p>
            <div className="venue-scan">
              <span className="scan-label">Scoring venues</span>
              <ol>
                {providers.map((provider, index) => (
                  <li key={provider.id} style={{ animationDelay: `${index * 0.35}s` }}>
                    {provider.label}
                  </li>
                ))}
              </ol>
              <span className="scan-clock">{(elapsed / 1000).toFixed(1)}s</span>
            </div>
            <p className="scan-note">
              Planning the route — primary, fallback, verifier — by failure cost.
            </p>
          </section>
        ) : null}

        {phase === "result" && result ? (
          <>
            <section className="run-header">
              <p className="desk-run-brief" title={query}>
                {query.trim() || fileName}
              </p>
              <div className="run-actions">
                <button type="button" onClick={editBrief}>
                  Edit brief
                </button>
                <button type="button" onClick={newRun}>
                  New run
                </button>
              </div>
            </section>

            <ExecutionReport route={result.route} />

            <p className="metrics-line" aria-label="Run metrics">
              <strong>{compactNumber(result.rows.length)} rows</strong>
              <span aria-hidden="true">·</span>
              {currency(result.estimated_cost)} per call
              {result.route.estimated_cost_per_grounded_row != null ? (
                <>
                  <span aria-hidden="true">·</span>
                  {currency(result.route.estimated_cost_per_grounded_row)} per grounded row
                </>
              ) : null}
              <span aria-hidden="true">·</span>
              {compactNumber(result.elapsed_ms)} ms
              <span aria-hidden="true">·</span>
              <em className={clsx("mode-flag", !result.is_demo && "is-live")}>
                {result.is_demo ? "demo" : "live"}
              </em>
            </p>

            {error ? (
              <div className="error-banner" role="alert">
                {error}
              </div>
            ) : null}
            {result.warnings.length ? (
              <div className="warning-stack" role="status">
                {result.warnings.map((warning) => (
                  <span key={warning}>{warning}</span>
                ))}
              </div>
            ) : null}

            <section className="ledger" aria-labelledby="ledger-heading">
              <div className="ledger-toolbar">
                <h2 id="ledger-heading">Results</h2>
                <span className="review-count" role="status">
                  {result.rows.length - dropped.size} kept
                  {dropped.size ? ` · ${dropped.size} dropped` : ""}
                </span>
                <div className="export-group">
                  <button
                    type="button"
                    onClick={() => void handleExport("csv")}
                    disabled={exporting || result.rows.length === dropped.size}
                  >
                    <ArrowDownToLine aria-hidden="true" size={15} />
                    CSV
                  </button>
                  <button
                    type="button"
                    onClick={() => void handleExport("pdf")}
                    disabled={exporting || result.rows.length === dropped.size}
                  >
                    <ArrowDownToLine aria-hidden="true" size={15} />
                    PDF
                  </button>
                </div>
              </div>
              <ResultsTable
                columns={result.columns}
                rows={result.rows}
                dropped={dropped}
                onToggleDrop={toggleDrop}
              />
            </section>
          </>
        ) : null}
      </main>
    </div>
  );
}

function ExecutionReport({ route }: { route: RouteDecision }) {
  return (
    <section className="exec-report" aria-labelledby="report-heading">
      <div className="report-head">
        <p className="report-overline" id="report-heading">
          Execution report
        </p>
        <h2>{formatStrategy(route.strategy)}</h2>
        <p className="report-reason">{route.reason}</p>
        <p className="report-signals">
          {route.job_type ?? "research"} · {formatLabel(route.source_shape)} ·{" "}
          {route.evidence_risk} risk
          {route.freshness_days != null ? ` · ≤${route.freshness_days}d fresh` : ""}
          {route.processor_tier ? ` · processor ${route.processor_tier}` : ""}
        </p>
        {route.caveats.length ? (
          <ul className="report-caveats" aria-label="Route caveats">
            {route.caveats.map((caveat) => (
              <li key={caveat}>{caveat}</li>
            ))}
          </ul>
        ) : null}
      </div>

      <ol className="step-rail" aria-label="Route steps">
        {route.steps.map((step, index) => (
          <StepItem key={`${step.role}-${step.provider}-${index}`} step={step} />
        ))}
      </ol>

      <details className="route-why">
        <summary>Venue scores — why this route</summary>
        <table className="considered-table">
          <caption className="sr-only">Considered venues with scores and costs</caption>
          <thead>
            <tr>
              <th scope="col">Venue</th>
              <th scope="col">Score</th>
              <th scope="col">Fit</th>
              <th scope="col">Quality</th>
              <th scope="col">Speed</th>
              <th scope="col">Est. cost</th>
              <th scope="col">Status</th>
            </tr>
          </thead>
          <tbody>
            {route.considered.map((candidate) => (
              <tr key={candidate.id} className={clsx(candidate.id === route.provider && "is-chosen")}>
                <th scope="row">{candidate.label}</th>
                <td>{candidate.score.toFixed(2)}</td>
                <td>{candidate.task_fit != null ? percent(candidate.task_fit) : "—"}</td>
                <td>{percent(candidate.quality)}</td>
                <td>{percent(candidate.speed)}</td>
                <td>{currency(candidate.estimated_cost)}</td>
                <td>{candidate.available ? "live" : "demo"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {route.knowledge_version ? (
          <p className="knowledge-line">
            Venue priors reviewed {route.knowledge_version} — labeled by origin, expire unless
            recalibrated by run outcomes.
          </p>
        ) : null}
      </details>
    </section>
  );
}

function StepItem({ step }: { step: RouteStep }) {
  return (
    <li className="step-item" data-role={step.role}>
      <span className="step-marker" aria-hidden="true" />
      <div className="step-body">
        <span className="step-role">{formatLabel(step.role)}</span>
        <strong>{step.label}</strong>
        <p>{step.reason}</p>
        {step.trigger ? <p className="step-trigger">{step.trigger}</p> : null}
      </div>
      <div className="step-figures">
        <span>{currency(step.estimated_cost)} call</span>
        {step.estimated_cost_per_grounded_row != null ? (
          <span>{currency(step.estimated_cost_per_grounded_row)} grounded</span>
        ) : null}
        <em className={step.available ? "is-live" : undefined}>
          {step.available ? "live" : "demo"}
        </em>
      </div>
    </li>
  );
}

function ResultsTable({
  columns,
  rows,
  dropped,
  onToggleDrop,
}: {
  columns: string[];
  rows: ResultRow[];
  dropped: ReadonlySet<number>;
  onToggleDrop: (index: number) => void;
}) {
  const dataColumns = columns.filter(
    (column) => !METADATA_COLUMNS.includes(column as (typeof METADATA_COLUMNS)[number]),
  );
  return (
    <div className="ledger-table-wrap">
      <table className="data-table">
        <caption className="sr-only">Research results</caption>
        <thead>
          <tr>
            {dataColumns.map((column) => (
              <th scope="col" key={column} title={column}>
                {formatColumnLabel(column)}
              </th>
            ))}
            <th scope="col">Conf.</th>
            <th scope="col">Via</th>
            <th scope="col">Sources</th>
            <th scope="col">
              <span className="sr-only">Review</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => {
            const isDropped = dropped.has(index);
            const combined: Record<string, CellValue> = { ...row.input, ...row.fields };
            return (
              <tr key={index} className={clsx(isDropped && "is-dropped")}>
                {dataColumns.map((column) => (
                  <td key={column} data-column={column}>
                    {renderDataCell(column, combined[column])}
                  </td>
                ))}
                <td data-column="confidence">
                  <span className="confidence">{percent(row.confidence)}</span>
                </td>
                <td data-column="via">
                  <AttributionCell row={row} />
                </td>
                <td data-column="citations">
                  <CitationsCell citations={row.citations} />
                </td>
                <td data-column="review">
                  <button
                    type="button"
                    className="review-toggle"
                    aria-pressed={isDropped}
                    title={isDropped ? "Restore row" : "Drop row from export"}
                    onClick={() => onToggleDrop(index)}
                  >
                    {isDropped ? (
                      <RotateCcw aria-hidden="true" size={14} />
                    ) : (
                      <X aria-hidden="true" size={14} />
                    )}
                    <span className="sr-only">{isDropped ? "Restore row" : "Drop row"}</span>
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function AttributionCell({ row }: { row: ResultRow }) {
  const others = row.contributing_providers.filter((provider) => provider !== row.provider);
  return (
    <span className="attribution-cell" title={`Filled by the ${row.step_role || "primary"} step`}>
      <span className="attribution-primary">{row.provider}</span>
      {row.step_role && row.step_role !== "primary" ? (
        <span className="attribution-role">{row.step_role}</span>
      ) : null}
      {row.verified ? (
        <span className="attribution-verified" title="Independently corroborated">
          <Check aria-hidden="true" size={11} />
          verified
        </span>
      ) : null}
      {others.length ? <span className="attribution-others">+{others.join(" +")}</span> : null}
    </span>
  );
}

function CitationsCell({ citations }: { citations: ResultRow["citations"] }) {
  if (!citations.length) {
    return <span className="muted-cell">No source</span>;
  }
  return (
    <span className="citation-stack">
      {citations.slice(0, 3).map((citation, index) =>
        citation.url ? (
          <a key={`${citation.url}-${index}`} href={citation.url} target="_blank" rel="noreferrer">
            {citation.title || "source"}
          </a>
        ) : (
          <span key={`${citation.title || "source"}-${index}`}>
            {citation.title || citation.excerpt || "source"}
          </span>
        ),
      )}
    </span>
  );
}

function renderDataCell(column: string, value: CellValue | undefined) {
  if (column === "url" && typeof value === "string" && value) {
    return (
      <a href={value} target="_blank" rel="noreferrer">
        open
      </a>
    );
  }
  if (column === "summary") {
    return <span className="summary-cell">{displayValue(value)}</span>;
  }
  return <span>{displayValue(value)}</span>;
}

function formatColumnLabel(column: string) {
  return column.replace(/_/g, " ");
}

function formatStrategy(strategy: string) {
  const formatted = strategy.replace(/_/g, " ");
  return formatted.charAt(0).toUpperCase() + formatted.slice(1);
}

function formatLabel(value: string) {
  return value.replace(/_/g, " ");
}

function evidenceRiskHint(risk: EvidenceRisk) {
  if (risk === "low") return "Desk scan — citations optional, fastest venues eligible.";
  if (risk === "medium") return "Sourcing — citations required, balanced venues preferred.";
  return "Diligence / IC — per-field citations and an independent verifier are mandatory.";
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "Something went wrong.";
}
