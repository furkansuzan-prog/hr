"use client";
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { Badge, Button, Card, CardBody, ErrorBox, Input, PageHeader } from "@/components/ui/primitives";

type Answer = { intent: string; language: string; answer: string; kind: string; caveats: string[]; sources: { kpi: string; formula_version: number }[]; tools_used: string[]; data: unknown; refused?: boolean };
const EXAMPLES = [
  "Son altı ayda personel devir oranı neden yükseldi?",
  "Hangi departmanlarda planlanan ve gerçekleşen iş gücü arasında en yüksek fark var?",
  "Önümüzdeki üç ay için kaç ilave çalışana ihtiyaç duyulabilir?",
  "Eğitim saatleri ile ekip seviyesindeki kalite göstergeleri arasında bir ilişki var mı?",
  "Fazla mesai artmasına rağmen üretim artışı neden sınırlı kaldı?",
  "Alt yükleniciler arasında hangi operasyonel riskler öne çıkıyor?",
  "Geçen ay alınan aksiyonların sonuçları nasıl değişti?",
  "Which subcontractors show operational risks?",
];

export default function Assistant() {
  const { t } = useI18n();
  const [q, setQ] = useState("");
  const [log, setLog] = useState<{ q: string; a: Answer }[]>([]);
  const m = useMutation({
    mutationFn: (question: string) => api<Answer>("/api/v1/assistant/ask", { method: "POST", body: JSON.stringify({ question }) }),
    onSuccess: (a, question) => setLog((l) => [{ q: question, a }, ...l]),
  });
  const ask = (s: string) => { if (s.trim().length >= 3) { m.mutate(s); setQ(""); } };
  return (
    <>
      <PageHeader title="HR Intelligence Assistant" subtitle={t("ai.notice")} />
      <Card className="p-3">
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); ask(q); }}>
          <Input aria-label={t("ai.placeholder")} placeholder={t("ai.placeholder")} value={q} onChange={(e) => setQ(e.target.value)} />
          <Button type="submit" disabled={m.isPending}>{t("ai.ask")}</Button>
        </form>
        <div className="mt-3 flex flex-wrap gap-2">
          {EXAMPLES.map((e) => <button key={e} onClick={() => ask(e)} className="rounded-full border border-border px-3 py-1 text-xs hover:bg-surface-2">{e}</button>)}
        </div>
      </Card>
      <ErrorBox error={m.error} />
      {m.isPending && <p className="mt-3 text-sm text-muted">{t("common.loading")}</p>}
      <div className="mt-4 space-y-3">
        {log.map(({ q, a }, i) => (
          <Card key={i}>
            <CardBody className="space-y-2">
              <div className="text-sm font-medium">{q}</div>
              <div className="flex flex-wrap gap-1">
                <Badge tone={a.refused ? "critical" : "info"}>{a.intent}</Badge>
                <Badge tone={a.kind === "forecast" ? "warning" : "good"}>{a.kind === "forecast" ? "FORECAST / TAHMİN" : "ACTUAL / GERÇEKLEŞEN"}</Badge>
                {a.sources.map((s) => <Badge key={s.kpi}>{s.kpi} v{s.formula_version}</Badge>)}
              </div>
              <p className="text-sm leading-relaxed">{a.answer}</p>
              {a.caveats.length > 0 && <ul className="list-disc pl-5 text-xs text-warn">{a.caveats.map((c, j) => <li key={j}>{c}</li>)}</ul>}
              <details className="text-xs text-muted">
                <summary className="cursor-pointer">Evidence — tools: {a.tools_used.join(", ") || "none"}</summary>
                <pre className="mt-2 max-h-72 overflow-auto rounded bg-surface-2 p-2">{JSON.stringify(a.data, null, 2)}</pre>
              </details>
            </CardBody>
          </Card>
        ))}
      </div>
    </>
  );
}
