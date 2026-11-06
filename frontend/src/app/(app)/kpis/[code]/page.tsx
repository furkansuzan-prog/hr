"use client";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { api, type KpiResult } from "@/lib/api";
import { entityTypeKey, useI18n, type Key } from "@/lib/i18n";
import { fmt } from "@/lib/utils";
import { Chart } from "@/components/chart";
import { FilterBar, filterQs, statusTone } from "@/components/kpi";
import { useFilters } from "@/components/providers";
import { Badge, Card, CardBody, CardHeader, CardTitle, ErrorBox, PageHeader } from "@/components/ui/primitives";

type Def = {
  code: string; name: string; description: string; purpose: string; unit: string; status: string; inclusion_rules: string; exclusion_rules: string;
  missing_data_behavior: string; data_sources: string[]; data_owner_role: string; standard_refs: string[]; target: number | null; warning: number | null; critical: number | null;
  formula_versions?: { version: number; formula: string; parameters: Record<string, unknown>; is_current: boolean; change_note: string; created_at: string }[];
};

export default function KpiDetail() {
  const { code } = useParams<{ code: string }>();
  const { t } = useI18n();
  const { filters } = useFilters();
  const q = filterQs(filters);
  const def = useQuery({ queryKey: ["def", code], queryFn: () => api<Def>(`/api/v1/kpi/definitions/${code}`) });
  const active = def.data?.status === "active";
  const cur = useQuery({ queryKey: ["ev", code, q], queryFn: () => api<KpiResult>(`/api/v1/kpi/evaluate/${code}${q}`), enabled: active });
  const series = useQuery({ queryKey: ["series", code, q], queryFn: () => api<KpiResult[]>(`/api/v1/kpi/series/${code}${q}`), enabled: active });
  const ents = useQuery({ queryKey: ["byent", code, q], queryFn: () => api<KpiResult[]>(`/api/v1/kpi/by-entity/${code}${q}`), enabled: active });
  const r = cur.data;

  return (
    <>
      <PageHeader title={`${code} · ${def.data?.name ?? ""}`} subtitle={def.data?.purpose} />
      {active ? <FilterBar /> : def.data && <p className="mb-4 rounded-md bg-surface-2 p-3 text-sm">Draft KPI — business definition only; not validated for calculation.</p>}
      <ErrorBox error={def.error || cur.error} />
      <div className="grid gap-4 lg:grid-cols-3">
        {r && (
          <Card>
            <CardHeader><CardTitle>{r.period_start} – {r.period_end}</CardTitle><Badge tone={statusTone(r)}>{t(`status.${r.status}` as Key)}</Badge></CardHeader>
            <CardBody className="space-y-2 text-sm">
              <div className="text-3xl font-semibold tabular-nums">{r.status === "ok" ? fmt(r.value) : "—"} <span className="text-sm text-muted">{r.unit}</span></div>
              {r.note && <p className="text-xs text-warn">{r.note}</p>}
              <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
                <dt className="text-muted">Numerator</dt><dd className="tabular-nums">{fmt(r.numerator)}</dd>
                <dt className="text-muted">Denominator</dt><dd className="tabular-nums">{fmt(r.denominator)}</dd>
                <dt className="text-muted">n</dt><dd>{fmt(r.sample_size, 0)}</dd>
                <dt className="text-muted">{t("kpi.quality")}</dt><dd>{fmt(r.quality_score, 0)}%</dd>
                {r.annualized_value !== null && (<><dt className="text-muted">{t("kpi.annualized")}</dt><dd>{fmt(r.annualized_value)}</dd></>)}
                <dt className="text-muted">Thresholds</dt><dd>{fmt(r.thresholds.warning)} / {fmt(r.thresholds.critical)}</dd>
                <dt className="text-muted">{t("kpi.formula")}</dt><dd>v{r.formula.version} · {r.formula.calculator}</dd>
                <dt className="text-muted">Calculated</dt><dd>{new Date(r.calculated_at).toLocaleString()}</dd>
              </dl>
            </CardBody>
          </Card>
        )}
        {series.data && (
          <Card className="lg:col-span-2">
            <CardHeader><CardTitle>Monthly series</CardTitle></CardHeader>
            <CardBody>
              <Chart ariaLabel={`${code} monthly series`} option={{
                legend: { show: false },
                xAxis: { type: "category", data: series.data.map((s) => s.period_start.slice(0, 7)) },
                yAxis: { type: "value", scale: true },
                series: [{ type: "bar", barMaxWidth: 22, data: series.data.map((s) => s.value), itemStyle: { borderRadius: [3, 3, 0, 0] } }],
              }} />
              {series.data.some((s) => s.status !== "ok") && <p className="text-xs text-muted">Empty bars = not computable / insufficient data / suppressed (never shown as 0).</p>}
            </CardBody>
          </Card>
        )}
      </div>
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        {def.data && (
          <Card>
            <CardHeader><CardTitle>Definition & {t("kpi.formula")}</CardTitle></CardHeader>
            <CardBody className="space-y-2 text-sm">
              <p>{def.data.description}</p>
              <p><b>Include:</b> {def.data.inclusion_rules}</p>
              <p><b>Exclude:</b> {def.data.exclusion_rules}</p>
              <p><b>Missing data:</b> {def.data.missing_data_behavior}</p>
              <p><b>Sources:</b> {def.data.data_sources.join(", ")} · <b>Owner:</b> {def.data.data_owner_role}</p>
              <p><b>Standards (indicative mapping, not a compliance claim):</b> {def.data.standard_refs.join(", ") || "—"}</p>
              <h4 className="pt-2 font-semibold">Formula versions</h4>
              <ul className="space-y-1 text-xs">
                {def.data.formula_versions?.map((v) => (
                  <li key={v.version}><Badge tone={v.is_current ? "good" : "neutral"}>v{v.version}</Badge> {v.change_note} — <code>{JSON.stringify(v.parameters)}</code></li>
                ))}
              </ul>
            </CardBody>
          </Card>
        )}
        {r && (
          <Card>
            <CardHeader><CardTitle>{t("kpi.lineage")} & breakdown</CardTitle></CardHeader>
            <CardBody>
              <pre className="max-h-80 overflow-auto rounded bg-surface-2 p-3 text-xs">{JSON.stringify({ lineage: r.lineage, breakdown: r.breakdown, scope: r.scope }, null, 2)}</pre>
            </CardBody>
          </Card>
        )}
      </div>
      {ents.data && ents.data.length > 0 && (
        <Card className="mt-4 overflow-x-auto">
          <CardHeader><CardTitle>By company (same filters)</CardTitle></CardHeader>
          <table className="mt-2 w-full text-sm">
            <thead className="bg-surface-2 text-left text-xs text-muted"><tr><th className="px-3 py-2">Company</th><th className="px-3 py-2">Type</th><th className="px-3 py-2 text-right">Value</th><th className="px-3 py-2">Status</th><th className="px-3 py-2 text-right">n</th><th className="px-3 py-2 text-right">Quality</th></tr></thead>
            <tbody>
              {ents.data.map((e) => (
                <tr key={e.legal_entity!.id} className="border-t border-border">
                  <td className="px-3 py-1.5">{e.legal_entity!.name}</td>
                  <td className="px-3 py-1.5 text-muted">{(() => { const k = entityTypeKey(e.legal_entity!.type); return k ? t(k) : e.legal_entity!.type; })()}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{e.status === "ok" ? fmt(e.value) : "—"}</td>
                  <td className="px-3 py-1.5"><Badge tone={statusTone(e)}>{t(`status.${e.status}` as Key)}</Badge></td>
                  <td className="px-3 py-1.5 text-right">{fmt(e.sample_size, 0)}</td>
                  <td className="px-3 py-1.5 text-right">{fmt(e.quality_score, 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </>
  );
}
