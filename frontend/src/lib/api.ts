import type {
  OutcomePayload,
  PreviewResponse,
  ProviderPublic,
  ResearchPayload,
  ResearchResponse,
} from "@/types/research";

const API_BASE = "/backend";

async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(payload?.detail ?? `Request failed with ${response.status}`);
  }
  return (await response.json()) as T;
}

export async function getProviders(): Promise<ProviderPublic[]> {
  const response = await fetch(`${API_BASE}/api/providers`, { cache: "no-store" });
  return readJson<ProviderPublic[]>(response);
}

export async function previewSpreadsheet(file: File): Promise<PreviewResponse> {
  const form = new FormData();
  form.append("file", file);
  const response = await fetch(`${API_BASE}/api/preview`, { method: "POST", body: form });
  return readJson<PreviewResponse>(response);
}

export async function runResearch(payload: ResearchPayload): Promise<ResearchResponse> {
  const response = await fetch(`${API_BASE}/api/research`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return readJson<ResearchResponse>(response);
}

export async function postOutcome(routePlanId: string, outcome: OutcomePayload): Promise<void> {
  // Calibration signal, not user-facing — failures must never disturb the run.
  try {
    await fetch(`${API_BASE}/api/telemetry/outcome`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ route_plan_id: routePlanId, ...outcome }),
      keepalive: true,
    });
  } catch {
    // Telemetry is best-effort.
  }
}

export async function exportResults(
  kind: "csv" | "pdf",
  payload: Pick<ResearchResponse, "columns" | "rows" | "route">,
): Promise<Blob> {
  const response = await fetch(`${API_BASE}/api/export/${kind}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      title: "Edna Search Results",
      columns: payload.columns,
      rows: payload.rows,
      route: payload.route,
    }),
  });
  if (!response.ok) {
    const error = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(error?.detail ?? `Export failed with ${response.status}`);
  }
  return response.blob();
}

export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}
