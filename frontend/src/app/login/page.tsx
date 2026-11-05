"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, setToken } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { Button, Card, ErrorBox, Input } from "@/components/ui/primitives";

export default function LoginPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const { t } = useI18n();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const r = await api<{ access_token: string }>("/api/v1/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });
      setToken(r.access_token);
      qc.clear();
      router.replace("/");
    } catch (x) {
      setErr(x);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-sm p-6">
        <div className="mb-1 text-xs font-semibold tracking-widest text-primary">HRSWI</div>
        <h1 className="mb-1 text-lg font-semibold">{t("login.title")}</h1>
        <p className="mb-5 text-xs text-warn">{t("common.demo")}</p>
        <form onSubmit={submit} className="space-y-3">
          <label className="block text-sm">
            {t("login.email")}
            <Input className="mt-1" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
          </label>
          <label className="block text-sm">
            {t("login.password")}
            <Input className="mt-1" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
          </label>
          <ErrorBox error={err} />
          <Button type="submit" className="w-full" disabled={busy}>{t("login.submit")}</Button>
        </form>
        <p className="mt-4 text-xs text-muted">
          Demo: admin@demo.local · hr.director@demo.local · mc.ops@demo.local · sub01.manager@demo.local
        </p>
      </Card>
    </main>
  );
}
