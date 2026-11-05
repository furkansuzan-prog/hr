"use client";
import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, Plus, ShieldPlus, X } from "lucide-react";
import { api, type LegalEntity } from "@/lib/api";
import { manageError, type ManagedUser, type Role, type RoleAssignment } from "@/lib/manage";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { PermGate } from "@/components/kpi-views";
import { ConfirmDialog, Dialog, Field } from "@/components/ui/dialog";
import { Badge, Button, Card, CardBody, CardHeader, CardTitle, ErrorBox, Input, PageHeader, Select } from "@/components/ui/primitives";

type User = ManagedUser;

export default function AdminPage() {
  return (
    <PermGate perm="admin.users">
      <Admin />
    </PermGate>
  );
}

function Admin() {
  const { t } = useI18n();
  const qc = useQueryClient();
  const users = useQuery({ queryKey: ["m-users"], queryFn: () => api<User[]>("/api/v1/manage/users"), retry: false });
  const roles = useQuery({ queryKey: ["m-roles"], queryFn: () => api<Role[]>("/api/v1/manage/roles"), retry: false });
  const ents = useQuery({ queryKey: ["entities"], queryFn: () => api<LegalEntity[]>("/api/v1/org/legal-entities") });
  const entName = (id: number | null) => ents.data?.find((e) => e.id === id)?.name ?? `#${id}`;
  const [creating, setCreating] = useState(false);
  const [granting, setGranting] = useState<User | null>(null);
  const [revoking, setRevoking] = useState<{ user: User; a: RoleAssignment } | null>(null);
  const [filter, setFilter] = useState("");

  const revoke = useMutation({
    mutationFn: (id: number) => api(`/api/v1/manage/role-assignments/${id}`, { method: "DELETE" }),
    onSuccess: () => { setRevoking(null); qc.invalidateQueries({ queryKey: ["m-users"] }); },
  });

  const list = (users.data ?? []).filter((u) => !filter || `${u.email} ${u.full_name}`.toLowerCase().includes(filter.toLowerCase()));

  return (
    <>
      <PageHeader title={t("nav.admin")} subtitle={t("admin.subtitle")}
        actions={<Button onClick={() => setCreating(true)} disabled={!!users.error}><Plus size={16} aria-hidden /> {t("admin.createUser")}</Button>} />
      <ErrorBox error={manageError(users.error, t("manage.unavailable"))} />
      <Card>
        <CardHeader>
          <CardTitle>{t("admin.users")} {users.data && <span className="text-muted">({users.data.length})</span>}</CardTitle>
          <Input aria-label={t("admin.search")} placeholder={t("admin.search")} className="h-9 w-56" value={filter} onChange={(e) => setFilter(e.target.value)} />
        </CardHeader>
        <CardBody className="p-0 pt-3">
          {users.isLoading && <p className="px-4 pb-4 text-sm text-muted">{t("common.loading")}</p>}
          {users.data && list.length === 0 && <p className="px-4 pb-4 text-sm text-muted">{t("common.noData")}</p>}
          {list.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-surface-2 text-left text-xs text-muted">
                  <tr>
                    <th scope="col" className="px-3 py-2 font-medium">{t("admin.user")}</th>
                    <th scope="col" className="px-3 py-2 font-medium">{t("admin.status")}</th>
                    <th scope="col" className="px-3 py-2 font-medium">{t("admin.roles")}</th>
                    <th scope="col" className="px-3 py-2 font-medium"><span className="sr-only">{t("admin.actions")}</span></th>
                  </tr>
                </thead>
                <tbody>
                  {list.map((u) => (
                    <tr key={u.id} className="border-t border-border align-top" data-testid="user-row">
                      <td className="px-3 py-2">
                        <div className="font-medium">{u.full_name}</div>
                        <div className="text-xs text-muted">{u.email}</div>
                      </td>
                      <td className="px-3 py-2"><Badge tone={u.is_active ? "good" : "neutral"}>{u.is_active ? t("admin.active") : t("admin.inactive")}</Badge></td>
                      <td className="px-3 py-2">
                        <ul className="flex flex-wrap gap-1.5" aria-label={`${t("admin.roles")}: ${u.email}`}>
                          {u.roles.length === 0 && <li className="text-xs text-muted">{t("admin.noRoles")}</li>}
                          {u.roles.map((a) => (
                            <li key={a.assignment_id} className={cn("inline-flex items-center gap-1 rounded-full border border-border bg-surface-2 py-0.5 pl-2 pr-1 text-xs", a.active === false && "pr-2 line-through opacity-60")}
                              title={a.active === false ? t("admin.inactiveRole") : undefined}>
                              <span className="font-medium">{a.role_code}</span>
                              <span className="text-muted">· {a.scope_type === "tenant" ? t("admin.scopeTenant") : entName(a.legal_entity_id)}</span>
                              {a.valid_to && <span className="text-muted">· ≤ {a.valid_to}</span>}
                              {a.active !== false && <button type="button" onClick={() => { revoke.reset(); setRevoking({ user: u, a }); }}
                                aria-label={`${t("admin.revoke")} ${a.role_code} (${u.email})`} className="rounded-full p-0.5 text-muted hover:bg-crit/15 hover:text-crit">
                                <X size={12} aria-hidden />
                              </button>}
                            </li>
                          ))}
                        </ul>
                      </td>
                      <td className="px-3 py-2 text-right">
                        <Button size="sm" variant="outline" onClick={() => setGranting(u)}><ShieldPlus size={14} aria-hidden /> {t("admin.grant")}</Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardBody>
      </Card>

      {roles.data && (
        <Card className="mt-4">
          <CardHeader><CardTitle>{t("admin.roleCatalog")}</CardTitle></CardHeader>
          <CardBody className="grid gap-2 text-xs sm:grid-cols-2 xl:grid-cols-3">
            {roles.data.map((r) => (
              <details key={r.id} className="rounded-md border border-border p-2">
                <summary className="cursor-pointer"><span className="font-mono font-medium">{r.code}</span> — {r.name} <span className="text-muted">({r.permissions.length})</span>{r.is_system && <Badge className="ml-1">{t("admin.system")}</Badge>}</summary>
                <p className="mt-1 break-words font-mono text-[11px] text-muted">{r.permissions.join(", ")}</p>
              </details>
            ))}
          </CardBody>
        </Card>
      )}

      <CreateUserDialog open={creating} onClose={() => setCreating(false)} />
      {granting && <GrantRoleDialog key={granting.id} user={granting} roles={roles.data ?? []} entities={ents.data ?? []} onClose={() => setGranting(null)} />}
      <ConfirmDialog open={!!revoking} onClose={() => setRevoking(null)} busy={revoke.isPending}
        title={t("admin.revokeTitle")}
        description={revoking ? `${revoking.a.role_code} · ${revoking.a.scope_type === "tenant" ? t("admin.scopeTenant") : entName(revoking.a.legal_entity_id)} — ${revoking.user.email}` : undefined}
        confirmLabel={t("admin.revoke")}
        onConfirm={() => revoking && revoke.mutate(revoking.a.assignment_id)}
        error={<><p className="text-sm">{t("admin.revokeWarn")}</p>{revoke.error && <div className="mt-2"><ErrorBox error={revoke.error} /></div>}</>} />
    </>
  );
}

function CreateUserDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const [form, setForm] = useState({ email: "", full_name: "", locale: "tr" });
  const [created, setCreated] = useState<{ id: number; initial_password: string; email: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const m = useMutation({
    mutationFn: () => api<{ id: number; initial_password: string }>("/api/v1/manage/users", { method: "POST", body: JSON.stringify(form) }),
    onSuccess: (r) => { setCreated({ ...r, email: form.email }); qc.invalidateQueries({ queryKey: ["m-users"] }); },
  });
  const close = () => { setForm({ email: "", full_name: "", locale: "tr" }); setCreated(null); setCopied(false); m.reset(); onClose(); };
  const submit = (e: FormEvent) => { e.preventDefault(); m.mutate(); };
  const copy = async () => {
    try { await navigator.clipboard.writeText(created!.initial_password); setCopied(true); } catch { setCopied(false); }
  };
  return (
    <Dialog open={open} onClose={close} title={created ? t("admin.userCreated") : t("admin.createUser")}
      description={created ? created.email : t("admin.createHint")}
      footer={created ? <Button onClick={close}>{t("common.done")}</Button> : (
        <>
          <Button variant="outline" onClick={close} disabled={m.isPending}>{t("common.cancel")}</Button>
          <Button type="submit" form="create-user" disabled={m.isPending}>{t("admin.create")}</Button>
        </>
      )}>
      {created ? (
        <div className="space-y-3 text-sm">
          <div role="alert" className="rounded-md border border-warn/50 bg-warn/10 p-3 text-warn">{t("admin.passwordOnce")}</div>
          <Field label={t("admin.initialPassword")}>
            <div className="flex gap-2">
              <Input readOnly value={created.initial_password} className="font-mono" aria-label={t("admin.initialPassword")} onFocus={(e) => e.currentTarget.select()} data-testid="initial-password" />
              <Button type="button" variant="outline" onClick={copy} aria-label={t("admin.copy")}>
                {copied ? <Check size={16} aria-hidden /> : <Copy size={16} aria-hidden />}
              </Button>
            </div>
          </Field>
          <p aria-live="polite" className="text-xs text-good">{copied ? t("admin.copied") : ""}</p>
        </div>
      ) : (
        <form id="create-user" onSubmit={submit} className="space-y-3">
          <Field label={t("login.email")}>
            <Input type="email" required autoFocus value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} autoComplete="off" />
          </Field>
          <Field label={t("admin.fullName")}>
            <Input required minLength={2} value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} autoComplete="off" />
          </Field>
          <Field label={t("admin.locale")}>
            <Select className="w-full" value={form.locale} onChange={(e) => setForm({ ...form, locale: e.target.value })}>
              <option value="tr">Türkçe</option><option value="en">English</option><option value="ru">Русский</option>
            </Select>
          </Field>
          <ErrorBox error={manageError(m.error, t("manage.unavailable"))} />
        </form>
      )}
    </Dialog>
  );
}

function GrantRoleDialog({ user, roles, entities, onClose }: { user: User; roles: Role[]; entities: LegalEntity[]; onClose: () => void }) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const [role, setRole] = useState("");
  const [scope, setScope] = useState<"tenant" | "legal_entity">("legal_entity");
  const [entity, setEntity] = useState("");
  const m = useMutation({
    mutationFn: () => api(`/api/v1/manage/users/${user.id}/roles`, {
      method: "POST",
      body: JSON.stringify({ role_code: role, scope_type: scope, legal_entity_id: scope === "legal_entity" ? Number(entity) : null }),
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["m-users"] }); onClose(); },
  });
  const sel = roles.find((r) => r.code === role);
  const valid = !!role && (scope === "tenant" || !!entity);
  return (
    <Dialog open onClose={onClose} title={t("admin.grantTitle")} description={`${user.full_name} · ${user.email}`}
      footer={
        <>
          <Button variant="outline" onClick={onClose} disabled={m.isPending}>{t("common.cancel")}</Button>
          <Button type="submit" form="grant-role" disabled={!valid || m.isPending}>{t("admin.grant")}</Button>
        </>
      }>
      <form id="grant-role" className="space-y-3" onSubmit={(e) => { e.preventDefault(); if (valid) m.mutate(); }}>
        <Field label={t("admin.role")}>
          <Select className="w-full" required autoFocus value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="">—</option>
            {roles.map((r) => <option key={r.code} value={r.code}>{r.code} — {r.name}</option>)}
          </Select>
        </Field>
        {sel && <p className="break-words rounded bg-surface-2 p-2 font-mono text-[11px] text-muted">{sel.permissions.join(", ")}</p>}
        <fieldset className="space-y-1 text-sm">
          <legend className="mb-1 text-xs font-medium text-muted">{t("admin.scope")}</legend>
          <label className="flex items-center gap-2"><input type="radio" name="scope" checked={scope === "legal_entity"} onChange={() => setScope("legal_entity")} /> {t("admin.scopeEntity")}</label>
          <label className="flex items-center gap-2"><input type="radio" name="scope" checked={scope === "tenant"} onChange={() => setScope("tenant")} /> {t("admin.scopeTenant")}</label>
        </fieldset>
        {scope === "legal_entity" && (
          <Field label={t("common.entity")}>
            <Select className="w-full" required value={entity} onChange={(e) => setEntity(e.target.value)}>
              <option value="">—</option>
              {entities.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
            </Select>
          </Field>
        )}
        {scope === "tenant" && <p className="text-xs text-warn">{t("admin.tenantWarn")}</p>}
        <ErrorBox error={manageError(m.error, t("manage.unavailable"))} />
      </form>
    </Dialog>
  );
}
