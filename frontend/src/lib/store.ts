"use client";
import { useCallback, useSyncExternalStore } from "react";

/** localStorage-backed state via useSyncExternalStore: SSR-safe (server snapshot = null) and lint-clean.
 * Every read/write is wrapped - storage may be unavailable (private mode, blocked site data). */
const EVT = "hrswi:storage";

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function subscribe(cb: () => void) {
  window.addEventListener("storage", cb);
  window.addEventListener(EVT, cb);
  return () => {
    window.removeEventListener("storage", cb);
    window.removeEventListener(EVT, cb);
  };
}

export function usePersisted(key: string): [string | null, (v: string | null) => void] {
  const value = useSyncExternalStore(subscribe, () => read(key), () => null);
  const set = useCallback(
    (v: string | null) => {
      try {
        if (v === null) localStorage.removeItem(key);
        else localStorage.setItem(key, v);
      } catch {}
      window.dispatchEvent(new Event(EVT));
    },
    [key],
  );
  return [value, set];
}

export function useMediaQuery(q: string): boolean {
  return useSyncExternalStore(
    (cb) => {
      const m = window.matchMedia(q);
      m.addEventListener("change", cb);
      return () => m.removeEventListener("change", cb);
    },
    () => window.matchMedia(q).matches,
    () => false,
  );
}
