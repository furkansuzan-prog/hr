"use client";
/* Terminate / transfer an employment. The people list does not carry record_version, so the dialog first loads the
   person history (which does) and sends it back for optimistic concurrency - a stale version is rejected by the API. */
import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, type LegalEntity } from "@/lib/api";
import { useI18n, type Key } from "@/lib/i18n";
import { manageError, TERMINATION_CATEGORIES } from "@/lib/manage";
import { Dialog, Field } from "./ui/dialog";
import { Button, ErrorBox, Input, Select } from "./ui/primitives";

export type EmploymentRow = { employment_id: number; person_id: number; name: string; legal_entity: string; legal_entity_id?: number; start_date: string; end_date: string | null };
type History = { person_id: number; employments: { employment_id: number; legal_entity: string; start_date: string; end_date: string | null; record_version: number }[] };

export function EmploymentActionDialog({ row, mode, onClose }: { row: EmploymentRow; mode: "terminate" | "transfer"; onClose: () => void }) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const hist = useQuery({ queryKey: ["person-history", row.person_id], queryFn: () => api<History>(`/api/v1/people/${row.person_id}/history`), staleTime: 0 });
  const ents = useQuery({ queryKey: ["entities"], queryFn: () => api<LegalEntity[]>("/api/v1/org/legal-entities"), enabled: mode === "transfer" });
  const emp = hist.data?.employments.find((e) => e.employment_id === row.employment_id);
  const [date, setDate] = useState("");
  const [category, setCategory] = useState("");
  const [target, setTarget] = useState("");

  const m = useMutation({
    mutationFn: () =>
      mode === "terminate"
        ? api(`/api/v1/manage/employments/${row.employment_id}/terminate`, {
            method: "POST", body: JSON.stringify({ end_date: date, termination_category: category, record_version: emp!.record_version }),
          })
        : api(`/api/v1/manage/employments/${row.employment_id}/transfer`, {
            method: "POST", body: JSON.stringify({ target_legal_entity_id: Number(target), transfer_date: date, record_version: emp!.record_version }),
          }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["people"] });
      qc.invalidateQueries({ queryKey: ["person-history", row.person_id] });
      onClose();
    },
    onError: (e) => { if (e instanceof ApiError && e.status === 409) qc.invalidateQueries({ queryKey: ["person-history", row.person_id] }); },
  });
  const valid = !!emp && !!date && (mode === "terminate" ? !!category : !!target);
  const submit = (e: FormEvent) => { e.preventDefault(); if (valid) m.mutate(); };
  const conflict = m.error instanceof ApiError && m.error.status === 409;
  const formId = `emp-${mode}-${row.employment_id}`;

  return (
    <Dialog open onClose={onClose}
      title={mode === "terminate" ? t("emp.terminateTitle") : t("emp.transferTitle")}
      description={`${row.name} · ${row.legal_entity} · ${t("emp.since")} ${row.start_date}`}
      footer={
        <>
          <Button variant="outline" onClick={onClose} disabled={m.isPending}>{t("common.cancel")}</Button>
          <Button type="submit" form={formId} disabled={!valid || m.isPending} className={mode === "terminate" ? "bg-crit text-white" : undefined}>
            {mode === "terminate" ? t("emp.terminate") : t("emp.transfer")}
          </Button>
        </>
      }>
      <form id={formId} onSubmit={submit} className="space-y-3">
        {hist.isLoading && <p className="text-sm text-muted">{t("common.loading")}</p>}
        <ErrorBox error={hist.error} />
        {hist.data && !emp && <ErrorBox error={t("emp.notVisible")} />}
        <Field label={mode === "terminate" ? t("emp.endDate") : t("emp.transferDate")} hint={mode === "transfer" ? t("emp.transferHint") : undefined}>
          <Input type="date" required autoFocus min={row.start_date} value={date} onChange={(e) => setDate(e.target.value)} />
        </Field>
        {mode === "terminate" ? (
          <Field label={t("emp.category")}>
            <Select className="w-full" required value={category} onChange={(e) => setCategory(e.target.value)}>
              <option value="">—</option>
              {TERMINATION_CATEGORIES.map((c) => <option key={c} value={c}>{t(`term.${c}` as Key)}</option>)}
            </Select>
          </Field>
        ) : (
          <Field label={t("emp.target")}>
            <Select className="w-full" required value={target} onChange={(e) => setTarget(e.target.value)}>
              <option value="">—</option>
              {ents.data?.filter((e) => e.type !== "client" && e.id !== row.legal_entity_id && e.name !== row.legal_entity)
                .map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
            </Select>
          </Field>
        )}
        {emp && <p className="text-[11px] text-muted">record_version {emp.record_version}</p>}
        {conflict ? <ErrorBox error={`${t("emp.conflict")} (${(m.error as ApiError).message})`} /> : <ErrorBox error={manageError(m.error, t("manage.unavailable"))} />}
      </form>
    </Dialog>
  );
}
