"use client";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { fmt } from "@/lib/utils";
import { Badge, Card, CardHeader, CardTitle, PageHeader } from "@/components/ui/primitives";

type Risk = { id: number; title: string; category: string; severity: string; status: string; target_date: string; source_kpi: string };
type Action = { id: number; problem: string; kpi_code: string; baseline: number | null; target: number | null; result: number | null; priority: string; status: string; due_date: string; effectiveness: string | null; steps_taken: string | null };

export default function Risks() {
  const { t } = useI18n();
  const risks = useQuery({ queryKey: ["risks"], queryFn: () => api<Risk[]>("/api/v1/risks") });
  const actions = useQuery({ queryKey: ["actions"], queryFn: () => api<Action[]>("/api/v1/actions") });
  return (
    <>
      <PageHeader title={t("nav.risks")} subtitle="Every action records baseline KPI, target, owner, dates, steps, result and effectiveness." />
      <Card className="overflow-x-auto">
        <CardHeader><CardTitle>Risk register</CardTitle></CardHeader>
        <table className="mt-2 w-full text-sm"><thead className="bg-surface-2 text-left text-xs text-muted"><tr>{["Risk", "Category", "Severity", "KPI", "Target date", "Status"].map((h) => <th key={h} className="px-3 py-2">{h}</th>)}</tr></thead>
          <tbody>{risks.data?.map((r) => <tr key={r.id} className="border-t border-border"><td className="px-3 py-1.5">{r.title}</td><td className="px-3 py-1.5">{r.category}</td><td className="px-3 py-1.5"><Badge tone={r.severity === "critical" ? "critical" : r.severity === "high" ? "warning" : "neutral"}>{r.severity}</Badge></td><td className="px-3 py-1.5 font-mono text-xs">{r.source_kpi}</td><td className="px-3 py-1.5">{r.target_date}</td><td className="px-3 py-1.5">{r.status}</td></tr>)}</tbody></table>
      </Card>
      <Card className="mt-4 overflow-x-auto">
        <CardHeader><CardTitle>Improvement actions</CardTitle></CardHeader>
        <table className="mt-2 w-full text-sm"><thead className="bg-surface-2 text-left text-xs text-muted"><tr>{["Problem", "KPI", "Baseline", "Target", "Result", "Priority", "Due", "Status", "Effectiveness"].map((h) => <th key={h} className="px-3 py-2">{h}</th>)}</tr></thead>
          <tbody>{actions.data?.map((a) => <tr key={a.id} className="border-t border-border"><td className="px-3 py-1.5">{a.problem}<div className="text-xs text-muted">{a.steps_taken}</div></td><td className="px-3 py-1.5 font-mono text-xs">{a.kpi_code}</td><td className="px-3 py-1.5">{fmt(a.baseline)}</td><td className="px-3 py-1.5">{fmt(a.target)}</td><td className="px-3 py-1.5">{fmt(a.result)}</td><td className="px-3 py-1.5">{a.priority}</td><td className="px-3 py-1.5">{a.due_date}</td><td className="px-3 py-1.5">{a.status}</td><td className="px-3 py-1.5"><Badge tone={a.effectiveness === "effective" ? "good" : a.effectiveness === "partially" ? "warning" : "neutral"}>{a.effectiveness ?? "—"}</Badge></td></tr>)}</tbody></table>
      </Card>
    </>
  );
}
