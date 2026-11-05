"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { cn, fmt } from "@/lib/utils";
import { Chart, PALETTE } from "@/components/chart";
import { FilterBar, KpiTile, filterQs } from "@/components/kpi";
import { NotesPanel, PermGate, useKpiTiles } from "@/components/kpi-views";
import { useFilters } from "@/components/providers";
import { Badge, Card, CardBody, CardHeader, CardTitle, ErrorBox, PageHeader } from "@/components/ui/primitives";

type Monthly = { month: string; planned: number | null; actual: number | null };
type Fc = {
  status: string;
  note?: string;
  model?: string;
  backtest_mape?: number | null;
  current?: number;
  delta_at_horizon?: number;
  history: { period: string; value: number }[];
  forecast: { step: number; period: string; mean: number; lo80: number; hi80: number; lo95: number; hi95: number }[];
};
const HORIZONS = [3, 6, 12] as const;

export default function Planning() {
  const { t } = useI18n();
  const { filters } = useFilters();
  const q = filterQs(filters);
  const [horizon, setHorizon] = useState<(typeof HORIZONS)[number]>(6);
  const kpi = useKpiTiles(["KPI-006"], q);
  const fc = useQuery({
    queryKey: ["fc-plan", q, horizon],
    queryFn: () => api<Fc>(`/api/v1/forecast/headcount${filterQs(filters, { horizon })}`),
  });
  const r = kpi.data?.[0];
  const b = (r?.breakdown ?? {}) as { monthly?: Monthly[]; planned_avg?: number; actual_avg?: number; deviation_pct?: number };
  const monthly = b.monthly ?? [];

  return (
    <PermGate perm="forecast.read">
      <PageHeader title={t("nav.planning")} subtitle={t("plan.subtitle")} />
      <FilterBar />
      <ErrorBox error={kpi.error} />
      <section aria-label="KPI" className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {r && <KpiTile r={r} />}
        {r?.status === "ok" && (
          <>
            <Stat label={t("plan.plannedAvg")} value={fmt(b.planned_avg, 0)} />
            <Stat label={t("plan.actualAvg")} value={fmt(b.actual_avg, 0)} />
            <Stat label={t("plan.deviationPct")} value={`${fmt(b.deviation_pct, 1)}%`} />
          </>
        )}
      </section>
      {r && <NotesPanel results={[r]} />}

      <Card className="mt-4">
        <CardHeader><CardTitle>{t("plan.pvaTitle")}</CardTitle><Badge tone="neutral">KPI-006 · {t("plan.actualLabel")}</Badge></CardHeader>
        <CardBody>
          {kpi.isLoading && <p className="text-sm text-muted">{t("common.loading")}</p>}
          {r && monthly.length === 0 && <p className="text-sm text-muted">{t("common.noData")}</p>}
          {monthly.length > 0 && (
            <Chart ariaLabel={t("plan.pvaTitle")} option={{
              xAxis: { type: "category", data: monthly.map((m) => m.month.slice(0, 7)) },
              yAxis: { type: "value", scale: true },
              series: [
                { name: t("plan.planned"), type: "line", step: "middle", symbol: "rect", symbolSize: 6, data: monthly.map((m) => m.planned), itemStyle: { color: PALETTE[4] }, lineStyle: { width: 2 } },
                { name: t("plan.actual"), type: "line", symbolSize: 6, data: monthly.map((m) => m.actual), itemStyle: { color: PALETTE[0] } },
              ],
            }} />
          )}
        </CardBody>
      </Card>

      <Card className="mt-4">
        <CardHeader className="flex-wrap">
          <CardTitle>{t("plan.fcTitle")}</CardTitle>
          <div className="flex items-center gap-2">
            <span id="hz-label" className="text-xs text-muted">{t("plan.horizon")}</span>
            <div role="radiogroup" aria-labelledby="hz-label" className="inline-flex overflow-hidden rounded-md border border-border">
              {HORIZONS.map((h) => (
                <button key={h} role="radio" aria-checked={horizon === h} onClick={() => setHorizon(h)}
                  className={cn("h-8 px-3 text-sm", horizon === h ? "bg-primary text-primary-fg" : "bg-surface hover:bg-surface-2")}>
                  {h} {t("plan.months")}
                </button>
              ))}
            </div>
          </div>
        </CardHeader>
        <CardBody>
          <ErrorBox error={fc.error} />
          {fc.isLoading && <p className="text-sm text-muted">{t("common.loading")}</p>}
          {fc.data && fc.data.status !== "ok" && <p className="text-sm text-muted">{fc.data.note ?? t("common.noData")}</p>}
          {fc.data?.status === "ok" && (
            <>
              <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
                <Badge tone="info">{t("plan.forecastLabel")}</Badge>
                <span className="text-muted">{fc.data.model} · {t("plan.backtest")} MAPE {fmt(fc.data.backtest_mape)}%</span>
                <span className="ml-auto text-muted">{t("plan.current")}: <b className="text-text">{fmt(fc.data.current, 0)}</b> · Δ {horizon} {t("plan.months")}: <b className="text-text">{fmt(fc.data.delta_at_horizon, 0)}</b></span>
              </div>
              <ForecastChart fc={fc.data} />
              <p className="mt-1 text-xs text-muted">{t("plan.fcExplain")}</p>
              <div className="mt-3 overflow-x-auto">
                <table className="w-full text-sm">
                  <caption className="sr-only">{t("plan.fcTitle")}</caption>
                  <thead className="bg-surface-2 text-left text-xs text-muted">
                    <tr>
                      <th scope="col" className="px-3 py-2">{t("common.period")}</th>
                      <th scope="col" className="px-3 py-2">{t("plan.kind")}</th>
                      <th scope="col" className="px-3 py-2 text-right">{t("plan.mean")}</th>
                      <th scope="col" className="px-3 py-2 text-right">80%</th>
                      <th scope="col" className="px-3 py-2 text-right">95%</th>
                    </tr>
                  </thead>
                  <tbody>
                    {fc.data.forecast.map((p) => (
                      <tr key={p.step} className="border-t border-border">
                        <td className="px-3 py-1.5">{p.period.slice(0, 7)}</td>
                        <td className="px-3 py-1.5"><Badge tone="info">{t("plan.forecastLabel")}</Badge></td>
                        <td className="px-3 py-1.5 text-right tabular-nums">{fmt(p.mean, 0)}</td>
                        <td className="px-3 py-1.5 text-right tabular-nums text-muted">{fmt(p.lo80, 0)} – {fmt(p.hi80, 0)}</td>
                        <td className="px-3 py-1.5 text-right tabular-nums text-muted">{fmt(p.lo95, 0)} – {fmt(p.hi95, 0)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </CardBody>
      </Card>
    </PermGate>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <Card className="p-4">
      <div className="text-xs font-medium text-muted">{label}</div>
      <div className="mt-2 text-2xl font-semibold tabular-nums">{value}</div>
    </Card>
  );
}

function ForecastChart({ fc }: { fc: Fc }) {
  const { t } = useI18n();
  const hist = fc.history.slice(-12);
  const n = fc.forecast.length;
  const x = [...hist.map((h) => h.period.slice(0, 7)), ...fc.forecast.map((p) => p.period.slice(0, 7))];
  const pad = (k: number) => Array(k).fill(null);
  const last = hist[hist.length - 1];
  const A = `${t("plan.actualLabel")}`;
  const F = `${t("plan.forecastLabel")}`;
  return (
    <Chart
      height={300}
      ariaLabel={t("plan.fcAria")}
      option={{
        xAxis: { type: "category", data: x },
        yAxis: { type: "value", scale: true },
        series: [
          {
            name: A, type: "line", symbolSize: 5, itemStyle: { color: PALETTE[0] },
            data: [...hist.map((h) => h.value), ...pad(n)],
            markArea: hist.length ? {
              silent: true, itemStyle: { color: "rgba(127,127,127,0.08)" },
              label: { show: true, position: "insideTop", color: "#7f8a99", fontSize: 11 },
              data: [[{ name: F, xAxis: x[hist.length] }, { xAxis: x[x.length - 1] }]],
            } : undefined,
          },
          {
            name: F, type: "line", symbolSize: 5, lineStyle: { type: "dashed" }, itemStyle: { color: PALETTE[1] },
            data: [...pad(hist.length - 1), last?.value ?? null, ...fc.forecast.map((p) => p.mean)],
          },
          { name: "lo95", type: "line", stack: "b95", symbol: "none", lineStyle: { opacity: 0 }, tooltip: { show: false }, data: [...pad(hist.length), ...fc.forecast.map((p) => p.lo95)] },
          { name: t("plan.band95"), type: "line", stack: "b95", symbol: "none", tooltip: { show: false }, lineStyle: { opacity: 0 }, itemStyle: { color: PALETTE[1], opacity: 0.25 }, areaStyle: { color: PALETTE[1], opacity: 0.12 }, data: [...pad(hist.length), ...fc.forecast.map((p) => +(p.hi95 - p.lo95).toFixed(2))] },
          { name: "lo80", type: "line", stack: "b80", symbol: "none", lineStyle: { opacity: 0 }, tooltip: { show: false }, data: [...pad(hist.length), ...fc.forecast.map((p) => p.lo80)] },
          { name: t("plan.band80"), type: "line", stack: "b80", symbol: "none", tooltip: { show: false }, lineStyle: { opacity: 0 }, itemStyle: { color: PALETTE[1], opacity: 0.45 }, areaStyle: { color: PALETTE[1], opacity: 0.22 }, data: [...pad(hist.length), ...fc.forecast.map((p) => +(p.hi80 - p.lo80).toFixed(2))] },
        ],
        legend: { data: [A, F, t("plan.band80"), t("plan.band95")] },
      }}
    />
  );
}
