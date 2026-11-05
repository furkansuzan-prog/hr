"use client";
import Link from "next/link";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, qs } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { fmt } from "@/lib/utils";
import { FilterBar, KpiTile, filterQs } from "@/components/kpi";
import { useCan, useFilters } from "@/components/providers";
import { EmploymentActionDialog } from "@/components/employment-actions";
import { Badge, Button, Card, ErrorBox, Input, PageHeader, Select } from "@/components/ui/primitives";
import type { KpiResult } from "@/lib/api";

type Row = { employment_id: number; person_id: number; name: string; employee_number: string | null; legal_entity: string; legal_entity_id?: number; contract_type: string; start_date: string; end_date: string | null; termination_category: string | null };

export default function People() {
  const { t } = useI18n();
  const { filters } = useFilters();
  const [status, setStatus] = useState("active");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const canWrite = useCan("people.write");
  const [action, setAction] = useState<{ row: Row; mode: "terminate" | "transfer" } | null>(null);
  const fq = filterQs(filters);
  const kpis = ["KPI-016", "KPI-017", "KPI-001", "KPI-002", "KPI-018", "KPI-003"];
  const tiles = useQuery({ queryKey: ["people-kpis", fq], queryFn: () => Promise.all(kpis.map((c) => api<KpiResult>(`/api/v1/kpi/evaluate/${c}${fq}`))) });
  const list = useQuery({
    queryKey: ["people", status, search, page, filters.entity_ids],
    queryFn: () => api<{ total: number; items: Row[] }>(`/api/v1/people${qs({ status, q: search, page, page_size: 50, entity_id: filters.entity_ids?.[0] })}`),
  });
  const pages = Math.max(1, Math.ceil((list.data?.total ?? 0) / 50));
  return (
    <>
      <PageHeader title={t("nav.people")} subtitle="Hires, separations, retention and absence — names masked without people.pii" />
      <FilterBar showProject={false} />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">{tiles.data?.map((r) => <KpiTile key={r.code} r={r} />)}</div>
      <Card className="mt-4">
        <div className="flex flex-wrap items-center gap-2 p-3">
          <Select aria-label="Status" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
            <option value="active">Active / Aktif</option><option value="left">Left / Ayrılan</option><option value="all">All / Tümü</option>
          </Select>
          <Input aria-label="Search" className="w-64" placeholder="Search name / no…" value={search} onChange={(e) => { setSearch(e.target.value); setPage(1); }} />
          <span className="ml-auto text-sm text-muted">{fmt(list.data?.total, 0)} records</span>
        </div>
        <ErrorBox error={list.error} />
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-surface-2 text-left text-xs text-muted"><tr>{["Name", "No", "Company", "Contract", "Start", "End", "Reason"].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}{canWrite && <th className="px-3 py-2"><span className="sr-only">{t("admin.actions")}</span></th>}</tr></thead>
            <tbody>
              {list.data?.items.map((r) => (
                <tr key={r.employment_id} className="border-t border-border">
                  <td className="px-3 py-1.5"><Link className="text-primary hover:underline" href={`/people?person=${r.person_id}`}>{r.name}</Link></td>
                  <td className="px-3 py-1.5 font-mono text-xs">{r.employee_number ?? "—"}</td>
                  <td className="px-3 py-1.5">{r.legal_entity}</td>
                  <td className="px-3 py-1.5">{r.contract_type}</td>
                  <td className="px-3 py-1.5">{r.start_date}</td>
                  <td className="px-3 py-1.5">{r.end_date ?? "—"}</td>
                  <td className="px-3 py-1.5">{r.termination_category ? <Badge tone={r.termination_category === "unknown" ? "warning" : "neutral"}>{r.termination_category}</Badge> : "—"}</td>
                  {canWrite && (
                    <td className="whitespace-nowrap px-3 py-1 text-right">
                      {!r.end_date && (
                        <>
                          <Button size="sm" variant="ghost" onClick={() => setAction({ row: r, mode: "transfer" })} aria-label={`${t("emp.transfer")}: ${r.name}`}>{t("emp.transfer")}</Button>
                          <Button size="sm" variant="ghost" className="text-crit" onClick={() => setAction({ row: r, mode: "terminate" })} aria-label={`${t("emp.terminate")}: ${r.name}`}>{t("emp.terminate")}</Button>
                        </>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="flex items-center justify-end gap-2 p-3 text-sm">
          <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>‹</Button>
          <span>{page} / {pages}</span>
          <Button variant="outline" size="sm" disabled={page >= pages} onClick={() => setPage(page + 1)}>›</Button>
        </div>
      </Card>
      {action && <EmploymentActionDialog key={`${action.mode}-${action.row.employment_id}`} row={action.row} mode={action.mode} onClose={() => setAction(null)} />}
    </>
  );
}
