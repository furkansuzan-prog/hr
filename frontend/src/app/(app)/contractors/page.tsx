"use client";
import { useQuery } from "@tanstack/react-query";
import { api, type KpiResult } from "@/lib/api";
import { useI18n, type Key } from "@/lib/i18n";
import { fmt } from "@/lib/utils";
import { FilterBar, filterQs, statusTone } from "@/components/kpi";
import { useFilters } from "@/components/providers";
import { Badge, Card, CardBody, CardHeader, CardTitle, ErrorBox, PageHeader } from "@/components/ui/primitives";

const CODES = ["KPI-016", "KPI-001", "KPI-007", "KPI-003", "KPI-009", "KPI-014A", "KPI-013"];
type Tree = { nodes: { id: number; code: string; name: string; type: string }[]; edges: { parent_id: number; child_id: number; tier: number; data_sharing_granted: boolean; scope_of_work: string }[] };

export default function Contractors() {
  const { t } = useI18n();
  const { filters } = useFilters();
  const q = filterQs({ ...filters, entity_ids: undefined });
  const data = useQuery({
    queryKey: ["contractor-cmp", q],
    queryFn: async () => Object.fromEntries(await Promise.all(CODES.map(async (c) => [c, await api<KpiResult[]>(`/api/v1/kpi/by-entity/${c}${q}`)] as const))),
  });
  const tree = useQuery({ queryKey: ["tree"], queryFn: () => api<Tree>("/api/v1/org/contractor-tree") });
  const rows = data.data?.["KPI-016"].filter((r) => r.legal_entity!.type !== "client") ?? [];
  const cell = (code: string, id: number) => data.data?.[code].find((r) => r.legal_entity!.id === id);
  const names = Object.fromEntries((tree.data?.nodes ?? []).map((n) => [n.id, n]));
  return (
    <>
      <PageHeader title={t("nav.contractors")} subtitle="Compare only with scope of work, occupation mix and project phase in mind. Firms that did not grant data sharing are not visible to the main contractor." />
      <FilterBar />
      <ErrorBox error={data.error} />
      <Card className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-surface-2 text-left text-xs text-muted">
            <tr><th className="px-3 py-2">Company</th>{CODES.map((c) => <th key={c} className="px-3 py-2 text-right">{data.data?.[c][0]?.name ?? c}<div className="font-mono text-[10px]">{c}</div></th>)}</tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.legal_entity!.id} className="border-t border-border">
                <td className="px-3 py-1.5">{r.legal_entity!.name}<div className="text-xs text-muted">{r.legal_entity!.type}</div></td>
                {CODES.map((c) => {
                  const v = cell(c, r.legal_entity!.id);
                  return (
                    <td key={c} className="px-3 py-1.5 text-right tabular-nums">
                      {!v ? "—" : v.status === "ok" ? <Badge tone={statusTone(v)}>{fmt(v.value, 1)}</Badge> : <span className="text-xs text-muted" title={t(`status.${v.status}` as Key)}>{v.status === "suppressed" ? "n<5" : "—"}</span>}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      <Card className="mt-4">
        <CardHeader><CardTitle>{t("nav.organization")} — contractor hierarchy (today)</CardTitle></CardHeader>
        <CardBody>
          <ul className="space-y-1 text-sm">
            {tree.data?.edges.map((e, i) => (
              <li key={i} className="flex flex-wrap items-center gap-2">
                <span className="text-muted">{names[e.parent_id]?.code}</span> → <b>{names[e.child_id]?.name}</b>
                <Badge>tier {e.tier}</Badge><Badge>{e.scope_of_work}</Badge>
                <Badge tone={e.data_sharing_granted ? "good" : "warning"}>{e.data_sharing_granted ? "data sharing granted" : "no data sharing"}</Badge>
              </li>
            ))}
          </ul>
        </CardBody>
      </Card>
    </>
  );
}
