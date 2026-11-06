"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, download } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { FilterBar, filterQs } from "@/components/kpi";
import { useCan, useFilters } from "@/components/providers";
import { Badge, Button, Card, ErrorBox, PageHeader } from "@/components/ui/primitives";
import { UsageNotice } from "@/components/usage-notice";

type Rep = { code: string; name: string; status: string; formats: string[] };

export default function Reports() {
  const { t } = useI18n();
  const { filters } = useFilters();
  const canExport = useCan("report.export");
  const cat = useQuery({ queryKey: ["reports"], queryFn: () => api<Rep[]>("/api/v1/reports") });
  const [err, setErr] = useState<unknown>(null);
  const get = async (code: string, fmt: string) => {
    setErr(null);
    try {
      await download(`/api/v1/reports/${code}${filterQs(filters, { format: fmt })}`, `${code}_${filters.period_start}_${filters.period_end}.${fmt}`);
    } catch (e) {
      setErr(e);
    }
  };
  return (
    <>
      <PageHeader title={t("nav.reports")} subtitle="Every report states period, filters, data sources, calculation time, formula versions and data quality." />
      <FilterBar />
      <ErrorBox error={err || cat.error} />
      {!canExport && <p className="mb-3 text-sm text-warn">You can view reports but not export them (report.export missing).</p>}
      <Card>
        <ul className="divide-y divide-border">
          {cat.data?.map((r) => (
            <li key={r.code} className="flex flex-wrap items-center gap-3 p-3">
              <span className="flex-1 text-sm font-medium">{r.name}</span>
              {r.status === "available" ? (
                r.formats.map((f) => <Button key={f} size="sm" variant="outline" disabled={!canExport} onClick={() => get(r.code, f)}>{f.toUpperCase()}</Button>)
              ) : (
                <Badge>{t("common.planned")}</Badge>
              )}
            </li>
          ))}
        </ul>
      </Card>
      <UsageNotice />
    </>
  );
}
