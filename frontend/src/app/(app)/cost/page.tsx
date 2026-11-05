"use client";
import { useI18n } from "@/lib/i18n";
import { FilterBar, filterQs } from "@/components/kpi";
import { ByEntityTable, InfoNote, PermGate, SeriesCard, TileGrid } from "@/components/kpi-views";
import { useFilters } from "@/components/providers";
import { PageHeader } from "@/components/ui/primitives";

const CODES = ["KPI-019", "KPI-010"];

export default function Cost() {
  const { t } = useI18n();
  const { filters } = useFilters();
  const q = filterQs(filters);
  return (
    <PermGate perm="cost.read">
      <PageHeader title={t("nav.cost")} subtitle={t("cost.subtitle")} />
      <InfoNote>
        <p>{t("cost.note")}</p>
        <ul className="mt-1 list-disc pl-5 text-muted">
          <li>{t("cost.noteFx")}</li>
          <li>{t("cost.noteUnits")}</li>
        </ul>
      </InfoNote>
      <FilterBar />
      <TileGrid codes={CODES} q={q} />
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        {CODES.map((c) => <SeriesCard key={c} code={c} q={q} />)}
      </div>
      <ByEntityTable codes={CODES} q={q} notes />
    </PermGate>
  );
}
