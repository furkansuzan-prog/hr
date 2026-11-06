"use client";
/* Building blocks for the module pages (HSE, training, cost, planning). Everything shown is a backend KPI result;
   non-ok states are rendered as text with the backend note - never coerced to 0. */
import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Lock } from "lucide-react";
import { api, type KpiResult } from "@/lib/api";
import { entityTypeKey, useI18n, type Key } from "@/lib/i18n";
import { fmt } from "@/lib/utils";
import { Chart } from "./chart";
import { KpiTile, statusTone } from "./kpi";
import { useMe } from "./providers";
import { Badge, Card, CardBody, CardHeader, CardTitle, ErrorBox } from "./ui/primitives";

/** Page-level permission gate (the API enforces the same rules; this only avoids a page full of 403s). */
export function PermGate({ perm, children }: { perm: string; children: ReactNode }) {
  const me = useMe();
  const { t } = useI18n();
  if (me.isLoading) return <p className="text-sm text-muted">{t("common.loading")}</p>;
  if (!me.data?.permissions.includes(perm))
    return (
      <Card className="mx-auto mt-10 max-w-md p-6 text-center">
        <Lock className="mx-auto mb-2 text-muted" size={22} aria-hidden />
        <p className="font-medium">{t("common.noAccess")}</p>
        <p className="mt-1 text-sm text-muted">{t("common.noAccessHint")} <code className="font-mono">{perm}</code></p>
      </Card>
    );
  return <>{children}</>;
}

export function useKpiTiles(codes: string[], q: string) {
  return useQuery({
    queryKey: ["tiles", codes.join(","), q],
    queryFn: () => Promise.all(codes.map((c) => api<KpiResult>(`/api/v1/kpi/evaluate/${c}${q}`))),
  });
}

export function TileGrid({ codes, q }: { codes: string[]; q: string }) {
  const { t } = useI18n();
  const tiles = useKpiTiles(codes, q);
  return (
    <>
      <ErrorBox error={tiles.error} />
      {tiles.isLoading && <p className="text-sm text-muted">{t("common.loading")}</p>}
      <section aria-label="KPI" className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {tiles.data?.map((r) => <KpiTile key={r.code} r={r} />)}
      </section>
      <NotesPanel results={tiles.data ?? []} />
    </>
  );
}

/** Classify a backend note so the reason is obvious at a glance (text itself always comes from the API). */
function noteKind(note: string): Key {
  const n = note.toLowerCase();
  if (n.includes("fx")) return "note.fx";
  if (n.includes("unit")) return "note.units";
  if (n.includes("small group") || n.includes("suppress")) return "note.suppressed";
  return "note.data";
}

/** Explains every non-ok KPI result with the backend note (missing FX rate, mixed output units, …). */
export function NotesPanel({ results, title }: { results: KpiResult[]; title?: string }) {
  const { t } = useI18n();
  const bad = results.filter((r) => r.status !== "ok" || r.note);
  if (!bad.length) return null;
  return (
    <Card className="mt-3 border-warn/40 bg-warn/5" role="note">
      <CardBody className="space-y-2 text-sm">
        <p className="font-medium">{title ?? t("note.title")}</p>
        <ul className="space-y-1.5">
          {bad.map((r, i) => (
            <li key={`${r.code}-${r.legal_entity?.id ?? i}`} className="flex flex-wrap items-start gap-2">
              <Badge tone={statusTone(r) === "neutral" ? "warning" : statusTone(r)}>{t(`status.${r.status}` as Key)}</Badge>
              <span className="font-mono text-xs text-muted">{r.code}</span>
              {r.legal_entity && <span className="text-xs">{r.legal_entity.name}</span>}
              {r.note && <Badge tone="neutral">{t(noteKind(r.note))}</Badge>}
              <span className="min-w-0 flex-1 basis-60 text-xs">{r.note ?? t("note.none")}</span>
            </li>
          ))}
        </ul>
      </CardBody>
    </Card>
  );
}

/** Monthly series for one KPI (one chart per KPI, so measures with different units are never mixed on one axis). */
export function SeriesCard({ code, q, type = "bar" }: { code: string; q: string; type?: "bar" | "line" }) {
  const { t } = useI18n();
  const s = useQuery({ queryKey: ["series", code, q], queryFn: () => api<KpiResult[]>(`/api/v1/kpi/series/${code}${q}`) });
  const first = s.data?.[0];
  const title = first ? `${first.name} (${first.unit})` : code;
  return (
    <Card>
      <CardHeader><CardTitle>{title}</CardTitle><span className="font-mono text-[10px] text-muted">{code}</span></CardHeader>
      <CardBody>
        <ErrorBox error={s.error} />
        {s.isLoading && <p className="text-sm text-muted">{t("common.loading")}</p>}
        {s.data && s.data.length === 0 && <p className="text-sm text-muted">{t("common.noData")}</p>}
        {s.data && s.data.length > 0 && s.data.every((p) => p.status !== "ok") && (
          <div className="flex h-[200px] flex-col items-center justify-center gap-2 rounded-md bg-surface-2 p-4 text-center text-sm">
            <Badge tone="warning">{t(`status.${s.data[0].status}` as Key)}</Badge>
            <p>{t("common.noMonthComputed")}</p>
            {s.data.find((p) => p.note)?.note && <p className="max-w-md text-xs text-muted">{s.data.find((p) => p.note)!.note}</p>}
          </div>
        )}
        {s.data && s.data.some((p) => p.status === "ok") && (
          <>
            <Chart ariaLabel={`${code} ${t("common.monthly")}`} height={240} option={{
              legend: { show: false },
              xAxis: { type: "category", data: s.data.map((p) => p.period_start.slice(0, 7)) },
              yAxis: { type: "value", scale: type === "line" },
              series: [{
                name: code, type, smooth: type === "line", barMaxWidth: 22, symbolSize: 5, connectNulls: false,
                data: s.data.map((p) => (p.status === "ok" ? p.value : null)),
                itemStyle: type === "bar" ? { borderRadius: [3, 3, 0, 0] } : undefined,
              }],
            }} />
            {s.data.some((p) => p.status !== "ok") && <p className="text-xs text-muted">{t("common.gapsNote")}</p>}
          </>
        )}
      </CardBody>
    </Card>
  );
}

/** Company comparison: one column per KPI, values straight from /kpi/by-entity. */
export function ByEntityTable({ codes, q, title, notes = false }: { codes: string[]; q: string; title?: string; notes?: boolean }) {
  const { t } = useI18n();
  const res = useQuery({
    queryKey: ["byent-multi", codes.join(","), q],
    queryFn: () => Promise.all(codes.map((c) => api<KpiResult[]>(`/api/v1/kpi/by-entity/${c}${q}`))),
  });
  const rows = new Map<number, { name: string; type: string; cells: (KpiResult | undefined)[] }>();
  res.data?.forEach((list, i) =>
    list.forEach((r) => {
      const le = r.legal_entity!;
      if (le.type === "client") return; // the project owner has no workforce of its own
      const row = rows.get(le.id) ?? { name: le.name, type: le.type, cells: Array(codes.length).fill(undefined) };
      row.cells[i] = r;
      rows.set(le.id, row);
    }),
  );
  const heads = res.data?.map((l, i) => (l[0] ? { code: codes[i], name: l[0].name, unit: l[0].unit } : { code: codes[i], name: codes[i], unit: "" }));
  return (
    <Card className="mt-4">
      <CardHeader><CardTitle>{title ?? t("common.byCompany")}</CardTitle></CardHeader>
      <CardBody className="p-0 pt-2">
        <ErrorBox error={res.error} />
        {res.isLoading && <p className="px-4 pb-4 text-sm text-muted">{t("common.loading")}</p>}
        {res.data && rows.size === 0 && <p className="px-4 pb-4 text-sm text-muted">{t("common.noData")}</p>}
        {rows.size > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-surface-2 text-left text-xs text-muted">
                <tr>
                  <th scope="col" className="px-3 py-2 font-medium">{t("common.entity")}</th>
                  <th scope="col" className="px-3 py-2 font-medium">{t("common.type")}</th>
                  {heads?.map((h) => (
                    <th scope="col" key={h.code} className="px-3 py-2 text-right font-medium">
                      <span className="block">{h.name}</span>
                      <span className="font-mono text-[10px]">{h.code} · {h.unit}</span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {[...rows.entries()].map(([id, row]) => (
                  <tr key={id} className="border-t border-border">
                    <td className="px-3 py-1.5">{row.name}</td>
                    <td className="px-3 py-1.5 text-xs text-muted">{(() => { const k = entityTypeKey(row.type); return k ? t(k) : row.type; })()}</td>
                    {row.cells.map((c, i) => (
                      <td key={i} className="px-3 py-1.5 text-right tabular-nums">
                        {!c ? "—" : c.status === "ok" ? (
                          <span title={c.sample_size !== null ? `n=${c.sample_size}` : undefined}>{fmt(c.value)}</span>
                        ) : (
                          <span title={c.note ?? undefined}><Badge tone="neutral">{t(`status.${c.status}` as Key)}</Badge></span>
                        )}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="px-4 py-2 text-[11px] text-muted">{t("common.byCompanyNote")}</p>
        {notes && (
          <div className="px-4 pb-4">
            <NotesPanel title={t("note.byCompany")} results={[...rows.values()].flatMap((r) => r.cells.filter((c): c is KpiResult => !!c))} />
          </div>
        )}
      </CardBody>
    </Card>
  );
}

/** Small explanatory callout (methodology notes). */
export function InfoNote({ children }: { children: ReactNode }) {
  return <div className="mb-4 rounded-md border border-primary/30 bg-primary/5 p-3 text-sm">{children}</div>;
}
