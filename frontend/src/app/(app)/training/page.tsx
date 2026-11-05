"use client";
import { useI18n } from "@/lib/i18n";
import { FilterBar, filterQs } from "@/components/kpi";
import { ByEntityTable, InfoNote, PermGate, SeriesCard, TileGrid } from "@/components/kpi-views";
import { useFilters } from "@/components/providers";
import { PageHeader } from "@/components/ui/primitives";

const CODES = ["KPI-008", "KPI-009"];

export default function Training() {
  const { t } = useI18n();
  const { filters } = useFilters();
  const q = filterQs(filters);
  return (
    <PermGate perm="training.read">
      <PageHeader title={t("nav.training")} subtitle={t("training.subtitle")} />
      <InfoNote>{t("training.note")}</InfoNote>
      <FilterBar />
      <TileGrid codes={CODES} q={q} />
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <SeriesCard code="KPI-008" q={q} />
        <SeriesCard code="KPI-009" q={q} type="line" />
      </div>
      <ByEntityTable codes={CODES} q={q} />
    </PermGate>
  );
}
