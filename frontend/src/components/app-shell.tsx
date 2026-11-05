"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import {
  Activity, BarChart3, Bot, Building2, ClipboardList, Cpu, Database, FileText, GraduationCap, HardHat, Leaf, LogOut,
  Menu, MonitorOff, Moon, Settings, ShieldAlert, Sun, TrendingUp, Users, Wallet, X,
} from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { api, getToken, setToken } from "@/lib/api";
import { ConfirmDialog } from "./ui/dialog";
import { ErrorBox } from "./ui/primitives";
import { useI18n, type Key, type Locale } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { useMe, useTheme } from "./providers";

type Item = { href: string; key: Key; icon: typeof Users; perm?: string; planned?: boolean };
const NAV: Item[] = [
  { href: "/", key: "nav.overview", icon: BarChart3, perm: "kpi.read" },
  { href: "/contractors?view=org", key: "nav.organization", icon: Building2, perm: "org.read" },
  { href: "/people", key: "nav.people", icon: Users, perm: "people.read" },
  { href: "/sustainability", key: "nav.sustainability", icon: Leaf, perm: "sustainability.read" },
  { href: "/productivity", key: "nav.productivity", icon: Activity, perm: "kpi.read" },
  { href: "/planning", key: "nav.planning", icon: TrendingUp, perm: "forecast.read" },
  { href: "/contractors", key: "nav.contractors", icon: HardHat, perm: "kpi.read" },
  { href: "/hse", key: "nav.hse", icon: ShieldAlert, perm: "hse.read" },
  { href: "/training", key: "nav.training", icon: GraduationCap, perm: "training.read" },
  { href: "/cost", key: "nav.cost", icon: Wallet, perm: "cost.read" },
  { href: "/assistant", key: "nav.ai", icon: Bot, perm: "assistant.use" },
  { href: "/risks", key: "nav.risks", icon: ClipboardList, perm: "risk.read" },
  { href: "/reports", key: "nav.reports", icon: FileText, perm: "report.read" },
  { href: "/imports", key: "nav.integrations", icon: Database, perm: "import.write" },
  { href: "/kpis", key: "nav.kpis", icon: Cpu, perm: "kpi.read" },
  { href: "/admin", key: "nav.admin", icon: Settings, perm: "admin.users" },
];

export function AppShell({ children }: { children: ReactNode }) {
  const router = useRouter();
  const path = usePathname();
  const { t, locale, setLocale } = useI18n();
  const { theme, toggle } = useTheme();
  const [open, setOpen] = useState(false);
  const [logoutAll, setLogoutAll] = useState(false);
  const [logoutErr, setLogoutErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const me = useMe();
  const qc = useQueryClient();

  useEffect(() => {
    if (!getToken()) router.replace("/login");
  }, [router]);

  const perms = new Set(me.data?.permissions ?? []);
  const items = NAV.filter((i) => i.planned || !i.perm || perms.has(i.perm));

  return (
    <div className="min-h-screen lg:flex">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:bg-surface focus:p-2">Skip to content</a>
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-40 w-64 transform bg-sidebar text-sidebar-text transition-transform lg:static lg:translate-x-0",
          open ? "translate-x-0" : "-translate-x-full",
        )}
        aria-label="Main navigation"
      >
        <div className="flex h-14 items-center justify-between px-4">
          <span className="font-semibold tracking-wide text-white">HRSWI</span>
          <button className="lg:hidden" onClick={() => setOpen(false)} aria-label="Close menu"><X size={18} /></button>
        </div>
        <nav className="space-y-0.5 px-2 pb-6 text-sm">
          {items.map((i) => {
            const active = i.href === "/" ? path === "/" : path.startsWith(i.href.split("?")[0]) && i.href !== "/contractors?view=org";
            const Icon = i.icon;
            if (i.planned)
              return (
                <span key={i.key} className="flex cursor-not-allowed items-center gap-3 rounded px-3 py-2 opacity-45" title={t("common.planned")}>
                  <Icon size={16} aria-hidden /> <span className="flex-1">{t(i.key)}</span>
                  <span className="text-[10px] uppercase">{t("common.planned")}</span>
                </span>
              );
            return (
              <Link key={i.key} href={i.href} onClick={() => setOpen(false)} aria-current={active ? "page" : undefined}
                className={cn("flex items-center gap-3 rounded px-3 py-2 hover:bg-white/5", active && "bg-white/10 text-white")}>
                <Icon size={16} aria-hidden /> {t(i.key)}
              </Link>
            );
          })}
        </nav>
      </aside>
      {open && <div className="fixed inset-0 z-30 bg-black/40 lg:hidden" onClick={() => setOpen(false)} />}
      <div className="min-w-0 flex-1">
        <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-border bg-surface/95 px-4 backdrop-blur">
          <button className="lg:hidden" onClick={() => setOpen(true)} aria-label="Open menu"><Menu size={20} /></button>
          <div className="hidden truncate text-xs font-medium text-warn sm:block">{t("common.demo")}</div>
          <div className="ml-auto flex items-center gap-2">
            <select aria-label="Language" value={locale} onChange={(e) => { setLocale(e.target.value as Locale); qc.resetQueries(); }}
              className="h-8 rounded border border-border bg-surface px-1 text-sm">
              <option value="tr">TR</option><option value="en">EN</option><option value="ru">RU</option>
            </select>
            <button onClick={toggle} aria-label="Toggle theme" className="rounded p-2 hover:bg-surface-2">
              {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
            </button>
            <span className="hidden text-sm text-muted md:inline">{me.data?.full_name}</span>
            <button onClick={() => { setLogoutErr(null); setLogoutAll(true); }} aria-label={t("common.logoutAll")} title={t("common.logoutAll")}
              className="rounded p-2 hover:bg-surface-2">
              <MonitorOff size={16} aria-hidden />
            </button>
            <button
              onClick={() => { setToken(null); qc.clear(); router.replace("/login"); }}
              className="flex items-center gap-1 rounded p-2 text-sm hover:bg-surface-2" aria-label={t("common.logout")}
            >
              <LogOut size={16} />
            </button>
          </div>
        </header>
        <ConfirmDialog open={logoutAll} onClose={() => setLogoutAll(false)} busy={busy}
          title={t("common.logoutAll")} description={t("common.logoutAllHint")} confirmLabel={t("common.logoutAll")}
          error={logoutErr ? <ErrorBox error={logoutErr} /> : undefined}
          onConfirm={async () => {
            setBusy(true);
            try {
              await api("/api/v1/auth/logout-all", { method: "POST" });
              setToken(null); qc.clear(); setLogoutAll(false); router.replace("/login");
            } catch (e) { setLogoutErr(e); } finally { setBusy(false); }
          }} />
        <main id="main" className="mx-auto max-w-[1400px] p-4 md:p-6">{children}</main>
      </div>
    </div>
  );
}
