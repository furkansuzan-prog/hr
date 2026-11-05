"use client";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { api, qs, type Filters, type LegalEntity, type KpiResult, type Project } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { cn, fmt, fmtCompact } from "@/lib/utils";
import { Badge, Card, Select } from "./ui/primitives";
import { useFilters } from "./providers";

export function statusTone(r: Pick<KpiResult, "status" | "threshold_status">) {
  if (r.status !== "ok") return "neutral" as const;
  if (r.threshold_status === "critical") return "critical" as const;
  if (r.threshold_status === "warning") return "warning" as const;
  if (r.threshold_status === "good") return "good" as const;
  return "info" as const;
}

/** Fatalities recorded in the result's scope and period (ADR-017 alert; present even for suppressed results). */
export function fatalities(r: Pick<KpiResult, "alerts">) {
  return (r.alerts ?? []).filter((a) => a.type === "fatality").reduce((n, a) => n + a.count, 0);
}

/** Shown where the traffic-light colour would be when the backend deliberately withholds it (status_reason). */
export function ReasonBadge({ r }: { r: KpiResult }) {
  const { t } = useI18n();
  if (!r.status_reason) return null;
  const min = r.formula.parameters?.threshold_min_exposure_hours;
  const hours = r.status_reason === "low_exposure" && typeof min === "number" && r.denominator !== null
    ? `${fmt(r.denominator, 0)} ${t("reason.hours")} ${fmt(min, 0)}`
    : null;
  return (
    <>
      <Badge tone="neutral" title={r.note ?? undefined}>{t(`reason.${r.status_reason}`)}</Badge>
      {hours && <span>({hours})</span>}
    </>
  );
}

const SAFETY_CALCULATORS = new Set(["ltifr", "trir"]);

/** KPI tile. Shows the backend value only; non-ok states are shown explicitly, never as 0. */
export function KpiTile({ r }: { r: KpiResult }) {
  const { t } = useI18n();
  const tone = statusTone(r);
  return (
    <Link href={`/kpis/${r.code}`} className="block focus-visible:outline-none">
      <Card className="h-full p-4 transition-shadow hover:shadow-md">
        <div className="flex items-start justify-between gap-2">
          <span className="text-xs font-medium text-muted">{r.name}</span>
          <span className="font-mono text-[10px] text-muted">{r.code}</span>
        </div>
        {r.status === "ok" ? (
          <div className="mt-2 flex flex-wrap items-baseline gap-x-1" title={fmt(r.value)}>
            <span className={cn("text-2xl font-semibold tabular-nums", tone === "critical" && "text-crit", tone === "warning" && "text-warn")}>
              {fmtCompact(r.value, r.unit === "persons" ? 0 : 2)}
            </span>
            <span className="text-xs text-muted">{r.unit}</span>
          </div>
        ) : (
          <div className="mt-2 text-sm font-medium text-muted">{t(`status.${r.status}`)}</div>
        )}
        <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[11px] text-muted">
          {fatalities(r) > 0 && <Badge tone="critical">{t("alert.fatality")}: {fatalities(r)}</Badge>}
          {r.status === "ok" && r.status_reason && <ReasonBadge r={r} />}
          {r.status === "ok" && SAFETY_CALCULATORS.has(r.formula.calculator) && r.numerator !== null && r.denominator !== null && (
            <span>{fmt(r.numerator, 0)} / {fmt(r.denominator, 0)} h</span>
          )}
          {r.annualized_value !== null && r.status === "ok" && (
            <span>{t("kpi.annualized")}: {fmt(r.annualized_value, 1)}{r.unit === "%" ? "%" : ""}</span>
          )}
          {r.quality_score !== null && r.quality_score < 80 && <Badge tone="warning">{t("kpi.quality")} {fmt(r.quality_score, 0)}%</Badge>}
          {r.sample_size !== null && <span>n={fmt(r.sample_size, 0)}</span>}
          <span className="ml-auto">v{r.formula.version}</span>
        </div>
      </Card>
    </Link>
  );
}

export function FilterBar({ showProject = true }: { showProject?: boolean }) {
  const { filters, setFilters } = useFilters();
  const { t } = useI18n();
  const ents = useQuery({ queryKey: ["entities"], queryFn: () => api<LegalEntity[]>("/api/v1/org/legal-entities") });
  const projects = useQuery({ queryKey: ["projects"], queryFn: () => api<Project[]>("/api/v1/org/projects") });
  const upd = (p: Partial<Filters>) => setFilters({ ...filters, ...p });
  return (
    <Card className="mb-4 flex flex-wrap items-end gap-3 p-3">
      <label className="text-xs text-muted">
        {t("common.period")}
        <div className="mt-1 flex items-center gap-1">
          <input type="date" aria-label="Period start" value={filters.period_start ?? ""} onChange={(e) => upd({ period_start: e.target.value })}
            className="h-9 rounded-md border border-border bg-surface px-2 text-sm text-text" />
          <span>–</span>
          {!filters.period_start && !filters.period_end && <span className="text-[11px] text-muted">default: last 12 months</span>}
          <input type="date" aria-label="Period end" value={filters.period_end ?? ""} onChange={(e) => upd({ period_end: e.target.value })}
            className="h-9 rounded-md border border-border bg-surface px-2 text-sm text-text" />
        </div>
      </label>
      <label className="text-xs text-muted">
        {t("common.entity")}
        <Select className="mt-1 block w-56" value={filters.entity_ids?.[0] ?? ""}
          onChange={(e) => upd({ entity_ids: e.target.value ? [Number(e.target.value)] : undefined })}>
          <option value="">{t("common.all")}</option>
          {ents.data?.filter((e) => e.type !== "client").map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
        </Select>
      </label>
      {showProject && (
        <label className="text-xs text-muted">
          {t("common.project")}
          <Select className="mt-1 block w-48" value={filters.project_id ?? ""}
            onChange={(e) => upd({ project_id: e.target.value ? Number(e.target.value) : null })}>
            <option value="">{t("common.all")}</option>
            {projects.data?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </Select>
        </label>
      )}
    </Card>
  );
}

export function filterQs(f: Filters, extra: Record<string, unknown> = {}) {
  return qs({ period_start: f.period_start, period_end: f.period_end, entity_ids: f.entity_ids, project_id: f.project_id, ...extra });
}
