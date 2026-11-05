"use client";
import { useI18n } from "@/lib/i18n";
import { FilterBar, fatalities, filterQs } from "@/components/kpi";
import { ByEntityTable, InfoNote, PermGate, SeriesCard, TileGrid, useKpiTiles } from "@/components/kpi-views";
import { useFilters } from "@/components/providers";
import { PageHeader } from "@/components/ui/primitives";

const CODES = ["KPI-014A", "KPI-014B"];

/** WP-2 R2 / ADR-017: non-dismissable band above the rates whenever a fatality is recorded in scope and period.
    Shows count and month only (no person, description or day). Shares the tile query, so no extra request. */
function FatalityBand({ q }: { q: string }) {
  const { t } = useI18n();
  const tiles = useKpiTiles(CODES, q);
  const r = tiles.data?.find((x) => fatalities(x) > 0);
  if (!r) return null;
  const months = Object.entries(r.alerts?.find((a) => a.type === "fatality")?.by_month ?? {});
  return (
    <div role="alert" className="mb-4 rounded-md border border-crit/40 bg-crit/10 p-3 text-sm text-crit">
      <p className="font-semibold">{t("alert.fatality")}: {fatalities(r)}</p>
      <p className="mt-1">{t("alert.fatalityBand")}</p>
      {months.length > 0 && <p className="mt-1 font-mono text-xs">{months.map(([m, n]) => `${m}: ${n}`).join(" · ")}</p>}
    </div>
  );
}

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
      <FatalityBand q={q} />
      <TileGrid codes={CODES} q={q} />
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        {CODES.map((c) => <SeriesCard key={c} code={c} q={q} />)}
      </div>
      <ByEntityTable codes={CODES} q={q} />
    </PermGate>
  );
}
