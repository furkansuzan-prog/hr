"use client";
import { useQuery } from "@tanstack/react-query";
import { api, type KpiResult } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { fmt } from "@/lib/utils";
import { FilterBar, KpiTile, filterQs } from "@/components/kpi";
import { useFilters } from "@/components/providers";
import { Badge, Card, CardBody, CardHeader, CardTitle, PageHeader } from "@/components/ui/primitives";

type Fw = { code: string; name: string; version: string; obligation: string; version_verified: boolean; note: string; disclaimer: string; coverage: { requirements: number; with_active_kpi: number }; requirements: { ref: string; kpis: { code: string; status: string }[]; data_available: boolean }[] };
const FAMILY = ["KPI-002", "KPI-008", "KPI-009", "KPI-014A", "KPI-014B", "KPI-018"];

export default function Sustainability() {
  const { t } = useI18n();
  const { filters } = useFilters();
  const q = filterQs(filters);
  const fws = useQuery({ queryKey: ["fws"], queryFn: () => api<Fw[]>("/api/v1/sustainability/frameworks") });
  const tiles = useQuery({ queryKey: ["sus-tiles", q], queryFn: () => Promise.all(FAMILY.map((c) => api<KpiResult>(`/api/v1/kpi/evaluate/${c}${q}`))) });
  const survey = useQuery({ queryKey: ["survey"], queryFn: () => api<{ dimension: string; respondents: number; mean_score: number }[]>("/api/v1/sustainability/survey") });
  return (
    <>
      <PageHeader title={t("nav.sustainability")} subtitle="KPI-015 is an indicator family — deliberately not collapsed into a single legal sustainability score." />
      <FilterBar />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">{tiles.data?.map((r) => <KpiTile key={r.code} r={r} />)}</div>
      <Card className="mt-4">
        <CardHeader><CardTitle>Employee experience survey (aggregates only, n ≥ 10)</CardTitle></CardHeader>
        <CardBody className="flex flex-wrap gap-6 text-sm">
          {survey.data?.map((s) => <div key={s.dimension}><div className="text-muted">{s.dimension}</div><div className="text-xl font-semibold">{fmt(s.mean_score)}/5</div><div className="text-xs text-muted">n={s.respondents}</div></div>)}
        </CardBody>
      </Card>
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        {fws.data?.map((f) => (
          <Card key={f.code}>
            <CardHeader>
              <CardTitle>{f.name} <span className="text-muted">({f.version})</span></CardTitle>
              <div className="flex gap-1"><Badge tone={f.obligation === "mandatory" ? "critical" : f.obligation === "conditional" ? "warning" : "neutral"}>{f.obligation}</Badge>{!f.version_verified && <Badge tone="warning">version unverified</Badge>}</div>
            </CardHeader>
            <CardBody className="space-y-2 text-sm">
              <p className="text-xs text-muted">{f.note}</p>
              <p>Data coverage: <b>{f.coverage.with_active_kpi}/{f.coverage.requirements}</b> requirements have an active KPI</p>
              <ul className="space-y-1 text-xs">
                {f.requirements.map((r) => <li key={r.ref} className="flex flex-wrap items-center gap-1"><Badge tone={r.data_available ? "good" : "warning"}>{r.data_available ? "data" : "gap"}</Badge>{r.ref} — {r.kpis.map((k) => k.code).join(", ")}</li>)}
              </ul>
              <p className="text-[11px] italic text-muted">{f.disclaimer}</p>
            </CardBody>
          </Card>
        ))}
      </div>
    </>
  );
}
