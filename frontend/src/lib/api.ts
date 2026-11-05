/** Thin API client. All numbers shown in the UI come from these backend calls - no KPI math in the browser. */
// Empty (default) = same-origin; Next rewrites /api/* to BACKEND_URL. Set to call a backend on another origin.
export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";
const TOKEN_KEY = "hrswi.token";

export class ApiError extends Error {
  /** `detail` keeps the raw FastAPI detail (it may be an object, e.g. {message, current_record_version}). */
  constructor(public status: number, message: string, public detail?: unknown) {
    super(message);
  }
}

function detailMessage(d: unknown, fallback: string): string {
  if (typeof d === "string") return d;
  if (d && typeof d === "object" && !Array.isArray(d) && typeof (d as { message?: unknown }).message === "string") return (d as { message: string }).message;
  if (Array.isArray(d)) return d.map((x) => (x && typeof x === "object" && "msg" in x ? String((x as { msg: unknown }).msg) : JSON.stringify(x))).join("; ");
  return d === undefined ? fallback : JSON.stringify(d);
}

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}
export function setToken(t: string | null) {
  try {
    if (t) localStorage.setItem(TOKEN_KEY, t);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable */
  }
}

export type Filters = {
  period_start?: string;
  period_end?: string;
  entity_ids?: number[];
  project_id?: number | null;
  occupation_group?: string | null;
};

export function qs(params: Record<string, unknown>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) {
      if (v.length) sp.set(k, v.join(","));
    } else sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

export async function api<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  headers.set("X-Locale", currentLocale());
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const res = await fetch(`${API_URL}${path}`, { ...init, headers });
  if (res.status === 401) {
    setToken(null);
    if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) window.location.assign(`${window.location.origin}/login`);
  }
  if (!res.ok) {
    let msg = res.statusText;
    let detail: unknown;
    try {
      detail = (await res.json()).detail;
      msg = detailMessage(detail, msg);
    } catch {
      /* not json */
    }
    throw new ApiError(res.status, msg, detail);
  }
  return (await res.json()) as T;
}

function currentLocale(): string {
  try {
    return localStorage.getItem("hrswi.locale") ?? "tr";
  } catch {
    return "tr";
  }
}

export async function download(path: string, filename: string) {
  const token = getToken();
  const res = await fetch(`${API_URL}${path}`, {
    headers: { "X-Locale": currentLocale(), ...(token ? { Authorization: `Bearer ${token}` } : {}) },
  });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      msg = (await res.json()).detail;
    } catch {}
    throw new ApiError(res.status, msg);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export type KpiStatusReason = "short_window" | "low_exposure" | "unknown_category_share" | "low_coverage";
export type KpiAlert = { type: "fatality"; count: number; by_month: Record<string, number> };

export type KpiResult = {
  code: string;
  name: string;
  unit: string;
  direction: string;
  status: "ok" | "not_computable" | "insufficient_data" | "suppressed";
  value: number | null;
  annualized_value: number | null;
  threshold_status: "good" | "warning" | "critical" | null;
  /** ADR-017: set when a computed value is deliberately not coloured (threshold_status is then null). */
  status_reason?: KpiStatusReason | null;
  /** ADR-017: rate-independent alerts (e.g. fatality on KPI-014A/B); never suppressed by small-group rules. */
  alerts?: KpiAlert[];
  numerator: number | null;
  denominator: number | null;
  sample_size: number | null;
  quality_score: number | null;
  breakdown: Record<string, unknown>;
  lineage: Record<string, unknown>;
  note: string | null;
  period_start: string;
  period_end: string;
  scope: { entity_ids: number[] | null; project_id: number | null; occupation_group: string | null };
  formula: { version: number; calculator: string; text: string; parameters: Record<string, unknown> };
  thresholds: { target: number | null; warning: number | null; critical: number | null };
  calculated_at: string;
  kind: "actual" | "forecast";
  legal_entity?: { id: number; code: string; name: string; type: string };
};

export type Me = {
  id: number;
  email: string;
  full_name: string;
  locale: string;
  permissions: string[];
  scopes: Record<string, number[] | "tenant">;
};
export type LegalEntity = { id: number; code: string; name: string; type: string };
export type Project = { id: number; code: string; name: string; phase: string | null };
