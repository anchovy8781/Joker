"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";

/** GET a resource, optionally polling. Returns {data, error, loading, reload}. */
export function useApi<T>(path: string | null, pollMs?: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState<boolean>(!!path);
  const alive = useRef(true);

  const reload = useCallback(async () => {
    if (!path) return;
    try {
      const d = await api<T>(path);
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
    reload();
    if (!pollMs) return () => { alive.current = false; };
    // Skip polls while the tab/app is in the background.
    const t = setInterval(() => { if (typeof document === "undefined" || !document.hidden) reload(); }, pollMs);
    return () => {
      alive.current = false;
      clearInterval(t);
    };
  }, [reload, pollMs]);

  return { data, error, loading, reload };
}
