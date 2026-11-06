"use client";
import { useQuery } from "@tanstack/react-query";
import { api, type KpiResult } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { fmt } from "@/lib/utils";
import { Chart, PALETTE } from "@/components/chart";
import { FilterBar, KpiTile, filterQs } from "@/components/kpi";
import { useCan, useFilters } from "@/components/providers";
import { Badge, Card, CardBody, CardHeader, CardTitle, ErrorBox, PageHeader } from "@/components/ui/primitives";
import { UsageNotice } from "@/components/usage-notice";

type Point = { period: string; value: number | null; status: string };
type Dash = {
  tiles: KpiResult[];
  trends: Record<string, Point[]>;
  headcount_by_entity: { entity: string; type: string; value: number | null }[];
};
type Fc = {
  status: string;
  note?: string;
  history: { period: string; value: number }[];
  forecast: { period: string; mean: number; lo80: number; hi80: number; lo95: number; hi95: number }[];
  backtest_mape: number | null;
  model: string;
};

export default function Overview() {
  const { t } = useI18n();
  const { filters } = useFilters();
  const q = filterQs(filters);
  const canForecast = useCan("forecast.read");
  const canRisk = useCan("risk.read");
  const dash = useQuery({ queryKey: ["dash", q], queryFn: () => api<Dash>(`/api/v1/dashboard/executive${q}`) });
  const fc = useQuery({
    queryKey: ["fc", q],
    queryFn: () => api<Fc>(`/api/v1/forecast/headcount${filterQs(filters, { horizon: 3 })}`),
    enabled: canForecast,
  });
  const risks = useQuery({ queryKey: ["risks"], queryFn: () => api<{ id: number; title: string; severity: string; target_date: string }[]>("/api/v1/risks"), enabled: canRisk });
  const actions = useQuery({ queryKey: ["actions"], queryFn: () => api<{ id: number; problem: string; kpi_code: string; baseline: number; target: number; result: number | null; status: string; priority: string }[]>("/api/v1/actions"), enabled: canRisk });

  const d = dash.data;
  const line = (codes: string[], names: string[]) => ({
    xAxis: { type: "category" as const, data: d?.trends[codes[0]].map((p) => p.period) },
    yAxis: { type: "value" as const, scale: true },
    series: codes.map((c, i) => ({ name: names[i], type: "line" as const, smooth: true, symbolSize: 5, connectNulls: false, data: d?.trends[c].map((p) => p.value) })),
  });

  return (
    <>
      <PageHeader title={t("dash.title")} subtitle={d?.tiles[0] ? `${d.tiles[0].period_start} – ${d.tiles[0].period_end}` : undefined} />
      <FilterBar />
      <ErrorBox error={dash.error} />
      {dash.isLoading && <p className="text-sm text-muted">{t("common.loading")}</p>}
      {d && (
        <>
          <section aria-label="KPI" className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
            {d.tiles.map((r) => <KpiTile key={r.code} r={r} />)}
          </section>
          <section className="mt-4 grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader><CardTitle>{t("dash.headcountTrend")}</CardTitle></CardHeader>
              <CardBody><Chart ariaLabel={t("dash.headcountTrend")} option={{ ...line(["KPI-016"], ["Headcount"]), legend: { show: false } }} /></CardBody>
            </Card>
            <Card>
              <CardHeader><CardTitle>{t("dash.turnoverTrend")}</CardTitle></CardHeader>
              <CardBody><Chart ariaLabel={t("dash.turnoverTrend")} option={{ ...line(["KPI-001"], ["KPI-001"]), legend: { show: false } }} /></CardBody>
            </Card>
            <Card>
              <CardHeader><CardTitle>{t("dash.otAbs")}</CardTitle></CardHeader>
              <CardBody><Chart ariaLabel={t("dash.otAbs")} option={line(["KPI-007", "KPI-003"], ["Overtime / Fazla mesai", "Absenteeism / Devamsızlık"])} /></CardBody>
            </Card>
            {canForecast && (
              <Card>
                <CardHeader>
                  <CardTitle>{t("dash.forecast")}</CardTitle>
                  {fc.data?.status === "ok" && <Badge tone="info">{fc.data.model} · MAPE {fmt(fc.data.backtest_mape)}%</Badge>}
                </CardHeader>
                <CardBody>
                  {fc.data?.status === "ok" ? (
                    <ForecastChart fc={fc.data} />
                  ) : (
                    <p className="text-sm text-muted">{fc.data?.note ?? t("common.loading")}</p>
                  )}
                </CardBody>
              </Card>
            )}
          </section>
          <section className="mt-4 grid gap-4 lg:grid-cols-3">
            <Card className="lg:col-span-1">
              <CardHeader><CardTitle>{t("dash.byEntity")}</CardTitle></CardHeader>
              <CardBody>
                <Chart
                  height={Math.max(220, d.headcount_by_entity.length * 22)}
                  ariaLabel={t("dash.byEntity")}
                  option={{
                    grid: { left: 150, right: 24, top: 8, bottom: 24 },
                    tooltip: { trigger: "item" },
                    yAxis: { type: "category", inverse: true, data: d.headcount_by_entity.map((e) => e.entity.replace(" (Demo)", "")), axisLabel: { width: 140, overflow: "truncate" } },
                    xAxis: { type: "value" },
                    series: [{ type: "bar", barMaxWidth: 14, data: d.headcount_by_entity.map((e) => ({ value: e.value, itemStyle: { color: e.type === "subcontractor" ? PALETTE[1] : PALETTE[0], borderRadius: [0, 3, 3, 0] } })) }],
                  }}
                />
              </CardBody>
            </Card>
            {canRisk && (
              <>
                <Card>
                  <CardHeader><CardTitle>{t("dash.risks")}</CardTitle></CardHeader>
                  <CardBody>
                    <ul className="space-y-2 text-sm">
                      {risks.data?.map((r) => (
                        <li key={r.id} className="flex items-center justify-between gap-2">
                          <span>{r.title}</span>
                          <Badge tone={r.severity === "critical" ? "critical" : r.severity === "high" ? "warning" : "neutral"}>{r.severity}</Badge>
                        </li>
                      ))}
                    </ul>
                  </CardBody>
                </Card>
                <Card>
                  <CardHeader><CardTitle>{t("dash.actions")}</CardTitle></CardHeader>
                  <CardBody>
                    <ul className="space-y-2 text-sm">
                      {actions.data?.map((a) => (
                        <li key={a.id}>
                          <div className="flex items-center justify-between gap-2"><span>{a.problem}</span><Badge>{a.priority}</Badge></div>
                          <div className="text-xs text-muted">
                            {a.kpi_code}: {fmt(a.baseline)} → hedef/target {fmt(a.target)} · sonuç/result {fmt(a.result)} · {a.status}
                          </div>
                        </li>
                      ))}
                    </ul>
                  </CardBody>
                </Card>
              </>
            )}
          </section>
        </>
      )}
      <UsageNotice />
    </>
  );
}

function ForecastChart({ fc }: { fc: Fc }) {
  const hist = fc.history.slice(-12);
  const x = [...hist.map((h) => h.period.slice(0, 7)), ...fc.forecast.map((p) => p.period.slice(0, 7))];
  const pad = (n: number) => Array(n).fill(null);
  const last = hist[hist.length - 1];
  return (
    <Chart
      ariaLabel="Headcount forecast with 80% and 95% intervals"
      option={{
        xAxis: { type: "category", data: x },
        yAxis: { type: "value", scale: true },
        series: [
          { name: "Gerçekleşen / Actual", type: "line", data: [...hist.map((h) => h.value), ...pad(fc.forecast.length)], symbolSize: 5 },
          { name: "Tahmin / Forecast", type: "line", lineStyle: { type: "dashed" }, data: [...pad(hist.length - 1), last?.value, ...fc.forecast.map((p) => p.mean)], symbolSize: 5 },
          { name: "lo95", type: "line", stack: "band", symbol: "none", lineStyle: { opacity: 0 }, data: [...pad(hist.length), ...fc.forecast.map((p) => p.lo95)], tooltip: { show: false } },
          { name: "95%", type: "line", stack: "band", symbol: "none", lineStyle: { opacity: 0 }, areaStyle: { color: PALETTE[0], opacity: 0.15 }, data: [...pad(hist.length), ...fc.forecast.map((p) => p.hi95 - p.lo95)] },
        ],
        legend: { data: ["Gerçekleşen / Actual", "Tahmin / Forecast", "95%"] },
      }}
    />
  );
}
