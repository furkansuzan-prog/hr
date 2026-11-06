"use client";
/** B-20 / ADR-020: data-feed coverage matrix (feed × employer × month) and manual declaration. Metadata only. */
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, qs } from "@/lib/api";
import { useI18n, type Key } from "@/lib/i18n";
import { Badge, Button, Card, CardBody, CardHeader, CardTitle, ErrorBox, Input, Select } from "@/components/ui/primitives";

export const FEEDS = ["employment", "leave", "timesheet", "training", "cost", "safety", "output", "downtime"] as const;
export type Feed = (typeof FEEDS)[number];
export type Completeness = "complete" | "partial" | "none";
type State = "complete" | "partial" | "none" | "confirmed_empty" | "undeclared";
type Cell = { feed_type: Feed; legal_entity_id: number; project_id: number | null; period_month: string; state: State; coverage_ratio: number | null; note: string | null };
type Matrix = { months: string[]; legal_entities: { id: number; code: string; name: string }[]; cells: Cell[] };

const TONE: Record<State, "good" | "warning" | "critical" | "neutral" | "info"> = {
  complete: "good", confirmed_empty: "info", partial: "warning", none: "critical", undeclared: "neutral",
};

/** First day of the month `back` months before the current one, as YYYY-MM-DD. */
export function monthStart(back = 0): string {
  const d = new Date();
  const m = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() - back, 1));
  return m.toISOString().slice(0, 10);
}
/** Last day of the month containing `ym` (YYYY-MM or YYYY-MM-DD), as YYYY-MM-DD. */
export function monthEnd(ym: string): string {
  const [y, m] = ym.split("-").map(Number);
  return new Date(Date.UTC(y, m, 0)).toISOString().slice(0, 10);
}

export function FeedCoverage() {
  const { t } = useI18n();
  const qc = useQueryClient();
  const [feed, setFeed] = useState<Feed>("leave");
  const ps = monthStart(5);
  const pe = monthEnd(monthStart(0));
  const q = useQuery({
    queryKey: ["coverage", feed, ps, pe],
    queryFn: () => api<Matrix>(`/api/v1/coverage${qs({ period_start: ps, period_end: pe, feed_types: feed })}`),
  });
  const [form, setForm] = useState({ entity: "", from: ps.slice(0, 7), to: ps.slice(0, 7), completeness: "complete" as Completeness, note: "" });
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const state = (eid: number, month: string): Cell | undefined =>
    q.data?.cells.find((c) => c.legal_entity_id === eid && c.period_month === month && c.project_id === null);
  const declare = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api("/api/v1/coverage", {
        method: "POST",
        body: JSON.stringify({
          feed_type: feed, legal_entity_id: Number(form.entity), period_start: `${form.from}-01`, period_end: monthEnd(form.to),
          completeness: form.completeness, note: form.note || null,
        }),
      });
      await qc.invalidateQueries({ queryKey: ["coverage"] });
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card className="mt-4 overflow-x-auto">
      <CardHeader>
        <CardTitle>{t("cov.title")}</CardTitle>
        <Select aria-label={t("cov.feed")} value={feed} onChange={(e) => setFeed(e.target.value as Feed)}>
          {FEEDS.map((f) => <option key={f} value={f}>{t(`cov.feed.${f}` as Key)}</option>)}
        </Select>
      </CardHeader>
      <CardBody className="space-y-3">
        <p className="text-xs text-muted">{t("cov.help")}</p>
        <ErrorBox error={q.error ?? err} />
        <table className="text-xs">
          <thead><tr><th className="px-2 py-1 text-left text-muted">{t("cov.entity")}</th>{q.data?.months.map((m) => <th key={m} className="px-2 py-1 text-left text-muted">{m.slice(0, 7)}</th>)}</tr></thead>
          <tbody>
            {q.data?.legal_entities.map((e) => (
              <tr key={e.id} className="border-t border-border">
                <td className="px-2 py-1" title={e.name}>{e.code}</td>
                {q.data.months.map((m) => {
                  const c = state(e.id, m);
                  const s: State = c?.state ?? "undeclared";
                  return (
                    <td key={m} className="px-2 py-1">
                      <Badge tone={TONE[s]} title={c?.note ?? undefined}>
                        {t(`cov.state.${s}` as Key)}{s === "partial" && c?.coverage_ratio != null ? ` ${Math.round(c.coverage_ratio * 100)}%` : ""}
                      </Badge>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
        <div className="flex flex-wrap items-end gap-2 text-xs">
          <label className="flex flex-col gap-1">{t("cov.entity")}
            <Select value={form.entity} onChange={(e) => setForm({ ...form, entity: e.target.value })}>
              <option value="">—</option>
              {q.data?.legal_entities.map((e) => <option key={e.id} value={e.id}>{e.code}</option>)}
            </Select>
          </label>
          <label className="flex flex-col gap-1">{t("cov.from")}<Input type="month" className="h-9 w-36" value={form.from} onChange={(e) => setForm({ ...form, from: e.target.value })} /></label>
          <label className="flex flex-col gap-1">{t("cov.to")}<Input type="month" className="h-9 w-36" value={form.to} onChange={(e) => setForm({ ...form, to: e.target.value })} /></label>
          <label className="flex flex-col gap-1">{t("cov.completeness")}
            <Select value={form.completeness} onChange={(e) => setForm({ ...form, completeness: e.target.value as Completeness })}>
              {(["complete", "partial", "none"] as const).map((c) => <option key={c} value={c}>{t(`cov.state.${c}`)}</option>)}
            </Select>
          </label>
          <label className="flex min-w-48 flex-1 flex-col gap-1">{t("cov.note")}<Input className="h-9" value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} /></label>
          <Button size="sm" onClick={declare} disabled={busy || !form.entity || !form.from || !form.to}>{t("cov.declare")}</Button>
        </div>
      </CardBody>
    </Card>
  );
}
