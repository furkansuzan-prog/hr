"use client";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, download } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { fmt } from "@/lib/utils";
import { useCan } from "@/components/providers";
import { IdentityReviewQueue } from "@/components/identity-reviews";
import { Badge, Button, Card, CardBody, CardHeader, CardTitle, ErrorBox, PageHeader, Select } from "@/components/ui/primitives";

type Upload = { batch_id: number; headers: string[]; total_rows: number; suggested_mapping: Record<string, string | null>; required_fields: string[]; preview: Record<string, string>[] };
type Summary = { batch_id: number; status: string; file_name: string; total_rows: number; valid_rows: number; error_rows: number; committed_rows: number; identity_reviews: number; attempt_count: number; error_message: string | null };
type Source = { id: number; code: string; name: string; kind: string; status: string; last_success_at: string | null; last_error: string | null };

export default function Imports() {
  const { t } = useI18n();
  const qc = useQueryClient();
  const canReview = useCan("people.write");
  const [up, setUp] = useState<Upload | null>(null);
  const [mapping, setMapping] = useState<Record<string, string | null>>({});
  const [sum, setSum] = useState<Summary | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const batches = useQuery({ queryKey: ["imports"], queryFn: () => api<Summary[]>("/api/v1/imports") });
  const sources = useQuery({ queryKey: ["sources"], queryFn: () => api<Source[]>("/api/v1/integrations/sources") });

  const run = async (fn: () => Promise<void>) => { setBusy(true); setErr(null); try { await fn(); } catch (e) { setErr(e); } finally { setBusy(false); qc.invalidateQueries({ queryKey: ["imports"] }); } };
  const onFile = (f: File) => run(async () => {
    const fd = new FormData();
    fd.append("file", f);
    fd.append("entity_type", "employment");
    const r = await api<Upload>("/api/v1/imports", { method: "POST", body: fd });
    setUp(r); setMapping(r.suggested_mapping); setSum(null);
  });
  const validate = () => run(async () => setSum(await api<Summary>(`/api/v1/imports/${up!.batch_id}/validate`, { method: "POST", body: JSON.stringify({ mapping }) })));
  const commit = () => run(async () => setSum(await api<Summary>(`/api/v1/imports/${up!.batch_id}/commit`, { method: "POST" })));
  const sync = (id: number) => run(async () => { await api(`/api/v1/integrations/sources/${id}/sync`, { method: "POST" }); qc.invalidateQueries({ queryKey: ["sources"] }); });

  return (
    <>
      <PageHeader title={t("nav.integrations")} subtitle="Upload → preview & field mapping → validation → commit. Same file cannot be committed twice; failed commits resume safely." />
      <ErrorBox error={err} />
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader><CardTitle>Employment import (CSV / XLSX)</CardTitle></CardHeader>
          <CardBody className="space-y-3">
            <input type="file" accept=".csv,.xlsx" aria-label="File" disabled={busy} onChange={(e) => e.target.files?.[0] && onFile(e.target.files[0])} className="text-sm" />
            {up && (
              <>
                <p className="text-sm">{up.total_rows} rows · batch #{up.batch_id}</p>
                <div className="grid gap-2 sm:grid-cols-2">
                  {Object.keys(up.suggested_mapping).map((f) => (
                    <label key={f} className="flex items-center justify-between gap-2 text-xs">
                      <span>{f}{up.required_fields.includes(f) && <span className="text-crit"> *</span>}</span>
                      <Select value={mapping[f] ?? ""} onChange={(e) => setMapping({ ...mapping, [f]: e.target.value || null })}>
                        <option value="">—</option>
                        {up.headers.map((h) => <option key={h} value={h}>{h}</option>)}
                      </Select>
                    </label>
                  ))}
                </div>
                <div className="overflow-x-auto"><table className="text-xs"><thead><tr>{up.headers.map((h) => <th key={h} className="px-2 py-1 text-left text-muted">{h}</th>)}</tr></thead>
                  <tbody>{up.preview.slice(0, 5).map((r, i) => <tr key={i}>{up.headers.map((h) => <td key={h} className="px-2 py-1">{r[h]}</td>)}</tr>)}</tbody></table></div>
                <div className="flex gap-2">
                  <Button onClick={validate} disabled={busy}>Validate</Button>
                  <Button onClick={commit} variant="outline" disabled={busy || !sum || !["validated", "failed"].includes(sum.status)}>Commit</Button>
                  {sum && sum.error_rows > 0 && <Button variant="ghost" onClick={() => download(`/api/v1/imports/${sum.batch_id}/errors.csv`, `import_${sum.batch_id}_errors.csv`)}>Error report</Button>}
                </div>
                {sum && <p className="text-sm">Status <Badge>{sum.status}</Badge> · valid {sum.valid_rows} · errors {sum.error_rows} · committed {sum.committed_rows} · identity reviews {sum.identity_reviews}</p>}
              </>
            )}
          </CardBody>
        </Card>
        <Card>
          <CardHeader><CardTitle>Data sources</CardTitle></CardHeader>
          <CardBody>
            <ul className="space-y-3 text-sm">
              {sources.data?.map((s) => (
                <li key={s.id}>
                  <div className="flex items-center justify-between gap-2"><span>{s.name}</span><Badge tone={s.status === "available" ? "good" : "critical"}>{s.status}</Badge></div>
                  {s.last_error && <div className="text-xs text-crit">{s.last_error}</div>}
                  <Button size="sm" variant="ghost" onClick={() => sync(s.id)} disabled={busy}>Sync</Button>
                </li>
              ))}
            </ul>
          </CardBody>
        </Card>
      </div>
      <Card className="mt-4 overflow-x-auto">
        <CardHeader><CardTitle>Recent batches</CardTitle></CardHeader>
        <table className="mt-2 w-full text-sm"><thead className="bg-surface-2 text-left text-xs text-muted"><tr>{["#", "File", "Status", "Rows", "Valid", "Errors", "Committed", "Attempts"].map((h) => <th key={h} className="px-3 py-2">{h}</th>)}</tr></thead>
          <tbody>{batches.data?.map((b) => (
            <tr key={b.batch_id} className="border-t border-border"><td className="px-3 py-1.5">{b.batch_id}</td><td className="px-3 py-1.5">{b.file_name}</td><td className="px-3 py-1.5"><Badge tone={b.status === "committed" ? "good" : b.status === "failed" ? "critical" : "neutral"}>{b.status}</Badge></td>
              <td className="px-3 py-1.5">{fmt(b.total_rows, 0)}</td><td className="px-3 py-1.5">{fmt(b.valid_rows, 0)}</td><td className="px-3 py-1.5">{fmt(b.error_rows, 0)}</td><td className="px-3 py-1.5">{fmt(b.committed_rows, 0)}</td><td className="px-3 py-1.5">{b.attempt_count}</td></tr>
          ))}</tbody></table>
      </Card>
      {canReview && <IdentityReviewQueue />}
    </>
  );
}
