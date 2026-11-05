"use client";
import Link from "next/link";
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { createColumnHelper, flexRender, getCoreRowModel, getFilteredRowModel, getSortedRowModel, useReactTable, type SortingState } from "@tanstack/react-table";
import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { Badge, Card, ErrorBox, Input, PageHeader } from "@/components/ui/primitives";

type Def = { code: string; name: string; family: string; module: string; unit: string; status: string; validated: boolean; sensitive: boolean; standard_refs: string[] };
const col = createColumnHelper<Def>();

export default function KpiCatalog() {
  const { t } = useI18n();
  const q = useQuery({ queryKey: ["defs"], queryFn: () => api<Def[]>("/api/v1/kpi/definitions") });
  const [filter, setFilter] = useState("");
  const [sorting, setSorting] = useState<SortingState>([]);
  const columns = useMemo(
    () => [
      col.accessor("code", { header: "Code", cell: (c) => <Link className="font-mono text-primary hover:underline" href={`/kpis/${c.getValue()}`}>{c.getValue()}</Link> }),
      col.accessor("name", { header: "KPI" }),
      col.accessor("family", { header: "Family" }),
      col.accessor("module", { header: "Module" }),
      col.accessor("unit", { header: "Unit" }),
      col.accessor("status", {
        header: "Status",
        cell: (c) => <Badge tone={c.getValue() === "active" ? "good" : "neutral"}>{c.getValue()}{c.row.original.sensitive ? " · cost" : ""}</Badge>,
      }),
      col.accessor((r) => r.standard_refs.join(", "), { id: "standards", header: "Standard refs (indicative)" }),
    ],
    [],
  );
  const table = useReactTable({
    data: q.data ?? [], columns, state: { globalFilter: filter, sorting }, onSortingChange: setSorting, onGlobalFilterChange: setFilter,
    getCoreRowModel: getCoreRowModel(), getFilteredRowModel: getFilteredRowModel(), getSortedRowModel: getSortedRowModel(),
  });
  const active = q.data?.filter((d) => d.status === "active").length ?? 0;
  return (
    <>
      <PageHeader title={t("nav.kpis")} subtitle={`${q.data?.length ?? 0} definitions · ${active} active & validated · drafts cannot run in production`}
        actions={<Input aria-label="Search" placeholder="Search…" className="w-64" value={filter} onChange={(e) => setFilter(e.target.value)} />} />
      <ErrorBox error={q.error} />
      <Card className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-surface-2 text-left text-xs text-muted">
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id}>
                {hg.headers.map((h) => (
                  <th key={h.id} className="cursor-pointer select-none px-3 py-2 font-medium" onClick={h.column.getToggleSortingHandler()}
                    aria-sort={h.column.getIsSorted() === "asc" ? "ascending" : h.column.getIsSorted() === "desc" ? "descending" : "none"}>
                    {flexRender(h.column.columnDef.header, h.getContext())}{{ asc: " ▲", desc: " ▼" }[h.column.getIsSorted() as string] ?? ""}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.map((r) => (
              <tr key={r.id} className="border-t border-border">
                {r.getVisibleCells().map((c) => <td key={c.id} className="px-3 py-2 align-top">{flexRender(c.column.columnDef.cell, c.getContext())}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </>
  );
}
