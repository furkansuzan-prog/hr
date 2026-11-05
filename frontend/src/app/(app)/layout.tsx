import { Suspense } from "react";
import { AppShell } from "@/components/app-shell";

/* The shell reads the URL (active nav item, dynamic KPI code). With cacheComponents enabled such reads must sit
   behind Suspense so the static fallback can be prerendered. All data is fetched client-side behind auth. */
export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <Suspense fallback={<div className="flex min-h-screen"><div className="hidden w-64 bg-sidebar lg:block" /><div className="flex-1 p-6 text-sm text-muted">…</div></div>}>
      <AppShell>{children}</AppShell>
    </Suspense>
  );
}
