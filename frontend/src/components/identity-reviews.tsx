"use client";
/* Identity review queue: import rows whose person could not be matched with certainty. A data steward decides to
   merge into the candidate person, create a new person, or reject the row. Every decision is confirmed first. */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useI18n, type Key } from "@/lib/i18n";
import { manageError, type IdentityReview } from "@/lib/manage";
import { ConfirmDialog } from "./ui/dialog";
import { Badge, Button, Card, CardBody, CardHeader, CardTitle, ErrorBox } from "./ui/primitives";

type Action = "merge" | "create_new" | "reject";
const ACTION_KEY: Record<Action, Key> = { merge: "idr.merge", create_new: "idr.createNew", reject: "idr.reject" };
const CONFIRM_KEY: Record<Action, Key> = { merge: "idr.confirmMerge", create_new: "idr.confirmCreate", reject: "idr.confirmReject" };

export function IdentityReviewQueue() {
  const { t } = useI18n();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["m-idr"], queryFn: () => api<IdentityReview[]>("/api/v1/manage/identity-reviews?status=open"), retry: false });
  const [pending, setPending] = useState<{ r: IdentityReview; action: Action } | null>(null);
  const m = useMutation({
    mutationFn: ({ id, action }: { id: number; action: Action }) =>
      api(`/api/v1/manage/identity-reviews/${id}/resolve`, { method: "POST", body: JSON.stringify({ action }) }),
    onSuccess: () => { setPending(null); qc.invalidateQueries({ queryKey: ["m-idr"] }); qc.invalidateQueries({ queryKey: ["imports"] }); },
  });
  const open = (r: IdentityReview, action: Action) => { m.reset(); setPending({ r, action }); };

  return (
    <Card className="mt-4" aria-labelledby="idr-title">
      <CardHeader>
        <CardTitle id="idr-title">{t("idr.title")} {q.data && <Badge tone={q.data.length ? "warning" : "good"}>{q.data.length}</Badge>}</CardTitle>
      </CardHeader>
      <CardBody className="space-y-3">
        <p className="text-xs text-muted">{t("idr.hint")}</p>
        <ErrorBox error={manageError(q.error, t("manage.unavailable"))} />
        {q.isLoading && <p className="text-sm text-muted">{t("common.loading")}</p>}
        {q.data?.length === 0 && <p className="text-sm text-muted">{t("idr.empty")}</p>}
        {!!q.data?.length && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-surface-2 text-left text-xs text-muted">
                <tr>
                  <th scope="col" className="px-3 py-2">#</th>
                  <th scope="col" className="px-3 py-2">{t("idr.reason")}</th>
                  <th scope="col" className="px-3 py-2">{t("idr.record")}</th>
                  <th scope="col" className="px-3 py-2">{t("idr.candidate")}</th>
                  <th scope="col" className="px-3 py-2">{t("idr.batch")}</th>
                  <th scope="col" className="px-3 py-2"><span className="sr-only">{t("admin.actions")}</span></th>
                </tr>
              </thead>
              <tbody>
                {q.data.map((r) => (
                  <tr key={r.id} className="border-t border-border align-top">
                    <td className="px-3 py-2 font-mono text-xs">{r.id}</td>
                    <td className="px-3 py-2"><Badge tone="warning">{r.reason}</Badge><div className="mt-1 text-[11px] text-muted">{r.created_at?.slice(0, 10)}</div></td>
                    <td className="px-3 py-2">
                      <dl className="grid grid-cols-[auto_1fr] gap-x-2 text-xs">
                        {Object.entries(r.payload ?? {}).slice(0, 6).map(([k, v]) => (
                          <div key={k} className="contents"><dt className="text-muted">{k}</dt><dd className="break-all">{String(v ?? "—")}</dd></div>
                        ))}
                      </dl>
                    </td>
                    <td className="px-3 py-2 text-xs">{r.candidate_person_id ? `#${r.candidate_person_id}` : "—"}</td>
                    <td className="px-3 py-2 text-xs">{r.import_batch_id ? `#${r.import_batch_id}` : "—"}</td>
                    <td className="px-3 py-2">
                      <div className="flex flex-wrap justify-end gap-1">
                        <Button size="sm" variant="outline" disabled={!r.candidate_person_id} title={!r.candidate_person_id ? t("idr.noCandidate") : undefined} onClick={() => open(r, "merge")}>{t("idr.merge")}</Button>
                        <Button size="sm" variant="outline" onClick={() => open(r, "create_new")}>{t("idr.createNew")}</Button>
                        <Button size="sm" variant="ghost" className="text-crit" onClick={() => open(r, "reject")}>{t("idr.reject")}</Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardBody>
      <ConfirmDialog
        open={!!pending}
        onClose={() => setPending(null)}
        busy={m.isPending}
        tone={pending?.action === "reject" ? "danger" : "primary"}
        title={pending ? `${t(ACTION_KEY[pending.action])} — #${pending.r.id}` : ""}
        description={pending ? t(CONFIRM_KEY[pending.action]) : undefined}
        confirmLabel={pending ? t(ACTION_KEY[pending.action]) : ""}
        onConfirm={() => pending && m.mutate({ id: pending.r.id, action: pending.action })}
        error={m.error ? <ErrorBox error={manageError(m.error, t("manage.unavailable"))} /> : undefined}
      />
    </Card>
  );
}
