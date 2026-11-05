"use client";
import { useQuery } from "@tanstack/react-query";
import { api, type KpiResult } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { Chart } from "@/components/chart";
import { FilterBar, KpiTile, filterQs } from "@/components/kpi";
import { useFilters } from "@/components/providers";
import { Card, CardBody, CardHeader, CardTitle, PageHeader } from "@/components/ui/primitives";

const CODES = ["KPI-004", "KPI-005", "KPI-013", "KPI-006", "KPI-007", "KPI-010"];

export default function Productivity() {
  const { t } = useI18n();
  const { filters } = useFilters();
  const q = filterQs(filters);
  const tiles = useQuery({ queryKey: ["prod", q], queryFn: () => Promise.all(CODES.map((c) => api<KpiResult>(`/api/v1/kpi/evaluate/${c}${q}`).catch(() => null))) });
  const s5 = useQuery({ queryKey: ["s5", q], queryFn: () => api<KpiResult[]>(`/api/v1/kpi/series/KPI-005${q}`) });
  const s7 = useQuery({ queryKey: ["s7", q], queryFn: () => api<KpiResult[]>(`/api/v1/kpi/series/KPI-007${q}`) });
  return (
    <>
      <PageHeader title={t("nav.productivity")} subtitle="Team / project level only. KPI-005 is computed only for work types with a production standard and quality-accepted output." />
      <FilterBar />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">{tiles.data?.filter((r): r is KpiResult => !!r).map((r) => <KpiTile key={r.code} r={r} />)}</div>
      {s5.data && s7.data && (
        <Card className="mt-4">
          <CardHeader><CardTitle>Productivity index vs overtime rate — efficiency / wellbeing tension</CardTitle></CardHeader>
          <CardBody>
            <Chart ariaLabel="Productivity index and overtime rate by month" option={{
              xAxis: { type: "category", data: s5.data.map((s) => s.period_start.slice(0, 7)) },
              yAxis: [{ type: "value", name: "index", scale: true }, { type: "value", name: "% OT", scale: true }] as never,
              series: [
                { name: "KPI-005", type: "line", data: s5.data.map((s) => s.value) },
                { name: "KPI-007", type: "bar", yAxisIndex: 1, barMaxWidth: 18, data: s7.data.map((s) => s.value) },
              ],
            }} />
            <p className="text-xs text-muted">Correlation is not causation; sustained high overtime is itself a health & safety risk signal.</p>
          </CardBody>
        </Card>
      )}
    </>
  );
}
