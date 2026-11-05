"use client";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, type Filters, type Me } from "@/lib/api";
import { I18nProvider } from "@/lib/i18n";
import { useMediaQuery, usePersisted } from "@/lib/store";

type FilterCtx = { filters: Filters; setFilters: (f: Filters) => void };
const FiltersContext = createContext<FilterCtx>({ filters: {}, setFilters: () => {} });
export const useFilters = () => useContext(FiltersContext);

/** Empty period = backend default (last 12 complete months); the browser never computes reporting periods. */
function FiltersProvider({ children }: { children: ReactNode }) {
  const [raw, setRaw] = usePersisted("hrswi.filters");
  const filters = useMemo<Filters>(() => {
    try {
      return raw ? (JSON.parse(raw) as Filters) : {};
    } catch {
      return {};
    }
  }, [raw]);
  const setFilters = useCallback((f: Filters) => setRaw(JSON.stringify(f)), [setRaw]);
  return <FiltersContext.Provider value={{ filters, setFilters }}>{children}</FiltersContext.Provider>;
}

type Theme = "light" | "dark";
const ThemeContext = createContext<{ theme: Theme; toggle: () => void }>({ theme: "light", toggle: () => {} });
export const useTheme = () => useContext(ThemeContext);

function ThemeProvider({ children }: { children: ReactNode }) {
  const [saved, setSaved] = usePersisted("hrswi.theme");
  const prefersDark = useMediaQuery("(prefers-color-scheme: dark)");
  const theme: Theme = saved === "dark" || saved === "light" ? saved : prefersDark ? "dark" : "light";
  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
  }, [theme]);
  const toggle = () => setSaved(theme === "dark" ? "light" : "dark");
  return <ThemeContext.Provider value={{ theme, toggle }}>{children}</ThemeContext.Provider>;
}

export function Providers({ children }: { children: ReactNode }) {
  const [qc] = useState(
    () => new QueryClient({ defaultOptions: { queries: { staleTime: 60_000, retry: 1, refetchOnWindowFocus: false } } }),
  );
  return (
    <QueryClientProvider client={qc}>
      <ThemeProvider>
        <I18nProvider>
          <FiltersProvider>{children}</FiltersProvider>
        </I18nProvider>
      </ThemeProvider>
    </QueryClientProvider>
  );
}

export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/api/v1/auth/me") });
}
export function useCan(perm: string) {
  const { data } = useMe();
  return !!data?.permissions.includes(perm);
}
