"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";

// Shared across components and page changes: the last response per path (shown instantly when
// a page is opened again, then refreshed) and the request currently in flight (so several
// components asking for the same path make one request).
const cache = new Map<string, unknown>();
const inflight = new Map<string, Promise<unknown>>();

function fetchShared<T>(path: string, fresh = false): Promise<T> {
  let p = inflight.get(path) as Promise<T> | undefined;
  if (!p || fresh) {
    p = api<T>(path).then((d) => { cache.set(path, d); return d; })
      .finally(() => inflight.delete(path));
    inflight.set(path, p);
  }
  return p;
}

/** Forget cached GET responses (e.g. after logout or an action that changes them). */
export function clearApiCache(prefix = "") {
  for (const k of [...cache.keys()]) if (k.startsWith(prefix)) cache.delete(k);
}

/** GET a resource, optionally polling. Returns {data, error, loading, reload}. */
export function useApi<T>(path: string | null, pollMs?: number) {
  const [data, setData] = useState<T | null>(() => (path ? (cache.get(path) as T | undefined) ?? null : null));
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState<boolean>(!!path && !cache.has(path));
  const alive = useRef(true);

  // fresh=true (after the user changed something) never reuses a request started earlier.
  const load = useCallback(async (fresh = false) => {
    if (!path) return;
    try {
      const d = await fetchShared<T>(path, fresh);
      if (alive.current) {
        setData(d);
        setError(null);
      }
    } catch (e) {
      if (alive.current) setError(e);
    } finally {
      if (alive.current) setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    alive.current = true;
    if (path && cache.has(path)) setData(cache.get(path) as T);
    load();
    if (!pollMs) return () => { alive.current = false; };
    // Skip polls while the tab/app is in the background; catch up as soon as it returns.
    const t = setInterval(() => { if (typeof document === "undefined" || !document.hidden) load(); }, pollMs);
    const onShow = () => { if (!document.hidden) load(); };
    document.addEventListener("visibilitychange", onShow);
    return () => {
      alive.current = false;
      clearInterval(t);
      document.removeEventListener("visibilitychange", onShow);
    };
  }, [load, pollMs, path]);

  const reload = useCallback(() => load(true), [load]);
  return { data, error, loading, reload };
}
