"use client";
import { useI18n } from "@/lib/i18n";
import { FilterBar, fatalities, filterQs } from "@/components/kpi";
import { ByEntityTable, InfoNote, PermGate, SeriesCard, TileGrid, useKpiTiles } from "@/components/kpi-views";
import { useFilters } from "@/components/providers";
import { PageHeader } from "@/components/ui/primitives";

const CODES = ["KPI-014A", "KPI-014B", "KPI-043"]; // KPI-043 near-miss rate: neutral context, never coloured (ADR-021)
const RATE_CODES = ["KPI-014A", "KPI-014B"];
const CATEGORIES = ["own_employee", "subcontractor_worker", "temporary_agency_worker", "third_party", "unknown"] as const;

/** WP-2 R2 / ADR-017: non-dismissable band above the rates whenever a fatality is recorded in scope and period.
    Shows count and month only (no person, description or day). Shares the tile query, so no extra request. */
function FatalityBand({ q }: { q: string }) {
  const { t } = useI18n();
  const tiles = useKpiTiles(CODES, q);
  const rates = tiles.data?.filter((x) => RATE_CODES.includes(x.code));
  const r = rates?.find((x) => fatalities(x) > 0);
  const re = rates?.map((x) => x.alerts?.find((a) => a.type === "fatality_reclassified")).find(Boolean);
  if (!r && !re) return null;
  const fatal = r?.alerts?.find((a) => a.type === "fatality");
  const months = Object.entries(fatal?.by_month ?? {});
  const cats = CATEGORIES.filter((c) => fatal?.by_worker_category?.[c]);
  return (
    <div role="alert" className="mb-4 rounded-md border border-crit/40 bg-crit/10 p-3 text-sm text-crit">
      {r && (
        <>
          <p className="font-semibold">{t("alert.fatality")}: {fatalities(r)}</p>
          <p className="mt-1">{t("alert.fatalityBand")}</p>
          {months.length > 0 && <p className="mt-1 font-mono text-xs">{months.map(([m, n]) => `${m}: ${n}`).join(" · ")}</p>}
          {cats.length > 0 && (
            <p className="mt-1 text-xs">{t("alert.byCategory")}: {cats.map((c) => `${t(`wcat.${c}`)} ${fatal!.by_worker_category![c]}`).join(" · ")}</p>
          )}
        </>
      )}
      {re && <p className={r ? "mt-2 text-xs" : "font-semibold"}>{t("alert.fatalityReclassified")}: {re.count}</p>}
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
