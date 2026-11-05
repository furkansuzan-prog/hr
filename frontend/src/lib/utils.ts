import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function fmt(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined) return "—";
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: digits }).format(v);
}

/** Compact form for large magnitudes so tiles never overflow (full value stays on the detail page). */
export function fmtCompact(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined) return "—";
  if (Math.abs(v) >= 100_000) return new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(v);
  return fmt(v, digits);
}
