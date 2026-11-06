"use client";
import { useI18n } from "@/lib/i18n";

/* Decision D-14 (docs/USAGE_POLICY.md): platform outputs are aggregate decision support - never for pay, bonus,
   promotion, discipline or individual performance evaluation. Same wording as the report footer
   (backend/app/services/reports.py USAGE_NOTICE). */
export function UsageNotice() {
  const { t } = useI18n();
  return (
    <p role="note" data-testid="usage-notice" className="mt-6 border-t border-border pt-3 text-xs text-muted">
      {t("usagePolicy.notice")}
    </p>
  );
}
