"use client";
import dynamic from "next/dynamic";
import { useSyncExternalStore } from "react";
import type { EChartsOption } from "echarts";

const ReactECharts = dynamic(() => import("echarts-for-react"), { ssr: false });

/** Validated categorical palette (works on light and dark surfaces). */
export const PALETTE = ["#2f6fdb", "#e0832b", "#2a9d78", "#c2477d", "#7c5cd6", "#8a9a2b", "#1f9bb5", "#b3563a"];

function subscribeClass(cb: () => void) {
  const mo = new MutationObserver(cb);
  mo.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
  return () => mo.disconnect();
}
function useDark() {
  return useSyncExternalStore(subscribeClass, () => document.documentElement.classList.contains("dark"), () => false);
}

export function Chart({ option, height = 260, ariaLabel }: { option: EChartsOption; height?: number; ariaLabel: string }) {
  const dark = useDark();
  const axis = dark ? "#8fa1b8" : "#5b6b7f";
  const grid = dark ? "#24324a" : "#e6ebf1";
  const themed: EChartsOption = {
    color: PALETTE,
    backgroundColor: "transparent",
    textStyle: { color: axis, fontFamily: "inherit" },
    grid: { left: 48, right: 16, top: 28, bottom: 32, containLabel: false },
    tooltip: { trigger: "axis", backgroundColor: dark ? "#17233a" : "#fff", borderColor: grid, textStyle: { color: dark ? "#e6edf6" : "#0f1b2d" } },
    legend: { top: 0, right: 0, textStyle: { color: axis }, itemWidth: 12, itemHeight: 8 },
    aria: { enabled: true, label: { description: ariaLabel } },
    ...option,
  };
  const ax = (a: unknown) =>
    a && typeof a === "object" && !Array.isArray(a)
      ? { axisLine: { lineStyle: { color: grid } }, axisLabel: { color: axis }, splitLine: { lineStyle: { color: grid } }, ...a }
      : a;
  // a page-level legend (e.g. legend.data) must keep the themed text colour - spread would drop it in dark mode
  if (option.legend && typeof option.legend === "object" && !Array.isArray(option.legend))
    themed.legend = { top: 0, right: 0, itemWidth: 12, itemHeight: 8, ...option.legend, textStyle: { color: axis, ...(option.legend.textStyle ?? {}) } };
  themed.xAxis = ax(option.xAxis) as EChartsOption["xAxis"];
  themed.yAxis = ax(option.yAxis) as EChartsOption["yAxis"];
  return (
    <div role="img" aria-label={ariaLabel}>
      <ReactECharts option={themed} style={{ height }} notMerge lazyUpdate />
    </div>
  );
}
