"use client";
import { useI18n } from "@/lib/i18n";
import { FilterBar, filterQs } from "@/components/kpi";
import { ByEntityTable, InfoNote, PermGate, SeriesCard, TileGrid } from "@/components/kpi-views";
import { useFilters } from "@/components/providers";
import { PageHeader } from "@/components/ui/primitives";

const CODES = ["KPI-014A", "KPI-014B"];

export default function Hse() {
  const { t } = useI18n();
  const { filters } = useFilters();
  const q = filterQs(filters);
  return (
    <PermGate perm="hse.read">
      <PageHeader title={t("nav.hse")} subtitle={t("hse.subtitle")} />
      <InfoNote>
        <p><b>{t("hse.separateTitle")}</b> {t("hse.separate")}</p>
        <p className="mt-1 text-muted">{t("hse.occupation")}</p>
      </InfoNote>
      <FilterBar />
      <TileGrid codes={CODES} q={q} />
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        {CODES.map((c) => <SeriesCard key={c} code={c} q={q} />)}
      </div>
      <ByEntityTable codes={CODES} q={q} />
    </PermGate>
  );
}
