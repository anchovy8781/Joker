"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";

// Shared across components and page changes: the last response per path (shown instantly when
// a page is opened again, then refreshed) and the request currently in flight (so several
// components asking for the same path make one request).
const cache = new Map<string, unknown>();
const inflight = new Map<string, Promise<unknown>>();

// The last responses are also kept in localStorage, so the app shows its screens instantly
// on the next launch (then refreshes them). Only public market data and the user's own
// summary are kept; everything is cleared on login/logout.
const STORE_KEY = "ytx-cache-v1";
const PERSIST = /^\/api\/(assets|rankings|market|account$|auth\/me$|announcements)/;
let restored = false;
function restore() {
  if (restored || typeof window === "undefined") return;
  restored = true;
  try {
    const saved = JSON.parse(localStorage.getItem(STORE_KEY) || "{}") as Record<string, unknown>;
    for (const [k, v] of Object.entries(saved)) if (!cache.has(k)) cache.set(k, v);
  } catch { /* storage unavailable: start empty */ }
}
let saveTimer: ReturnType<typeof setTimeout> | null = null;
function persistSoon() {
  if (typeof window === "undefined" || saveTimer) return;
  saveTimer = setTimeout(() => {
    saveTimer = null;
    try {
      const out: Record<string, unknown> = {};
      let n = 0;
      for (const [k, v] of [...cache].filter(([k]) => PERSIST.test(k)).slice(-40)) { out[k] = v; n++; }
      localStorage.setItem(STORE_KEY, JSON.stringify(out));
    } catch { /* quota or private mode: ignore */ }
  }, 1500);
}

// Every request gets a sequence number; only the newest one per path may write the cache
// (an older poll finishing late must not overwrite data fetched after an order). A sign-in
// change bumps the generation, so answers for the previous user are dropped.
let generation = 0;
let seqCounter = 0;
const latest = new Map<string, number>();

function fetchShared<T>(path: string, fresh = false): Promise<T | undefined> {
  let p = inflight.get(path) as Promise<T | undefined> | undefined;
  if (!p || fresh) {
    const seq = ++seqCounter;
    const gen = generation;
    latest.set(path, seq);
    const q: Promise<T | undefined> = api<T>(path).then((d) => {
      if (gen !== generation) return undefined; // answered for the previous session
      if (latest.get(path) === seq) {
        cache.delete(path); // re-insert: the most recently used paths are the ones persisted
        cache.set(path, d);
        if (PERSIST.test(path)) persistSoon();
      }
      return cache.has(path) ? (cache.get(path) as T) : d;
    }).finally(() => { if (inflight.get(path) === q) inflight.delete(path); });
    inflight.set(path, q);
    p = q;
  }
  return p;
}

/** Forget cached GET responses (e.g. after logout or an action that changes them). */
export function clearApiCache(prefix = "") {
  if (!prefix) {
    generation++;
    inflight.clear();
    latest.clear();
  }
  for (const k of [...cache.keys()]) if (k.startsWith(prefix)) cache.delete(k);
  try { if (typeof window !== "undefined") localStorage.removeItem(STORE_KEY); } catch { /* ignore */ }
}

/** Last known response for a path (restored from the previous launch if any). */
export function cached<T>(path: string): T | undefined {
  restore();
  return cache.get(path) as T | undefined;
}

export function remember(path: string, data: unknown) {
  cache.set(path, data);
  if (PERSIST.test(path)) persistSoon();
}

/** Warm the cache for the screens people open next (called once the app has started). */
export function prefetch(paths: string[]) {
  for (const p of paths) if (!cache.has(p) && !inflight.has(p)) fetchShared(p).catch(() => {});
}

/** GET a resource, optionally polling. Returns {data, error, loading, reload}. */
export function useApi<T>(path: string | null, pollMs?: number) {
  restore();
  const [data, setData] = useState<T | null>(() => (path ? (cache.get(path) as T | undefined) ?? null : null));
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState<boolean>(!!path && !cache.has(path));
  const alive = useRef(true);

  // fresh=true (after the user changed something) never reuses a request started earlier.
  const load = useCallback(async (fresh = false) => {
    if (!path) return;
    try {
      const d = await fetchShared<T>(path, fresh);
      if (d === undefined) return; // dropped: belonged to a previous sign-in
      if (alive.current) {
        setData(d);
        setError(null);
      }
    } catch (e) {
      // A failed refresh while we still have data (e.g. during a deploy) is not worth an
      // error banner: keep showing the last data; the next poll catches up.
      if (alive.current && !cache.has(path)) setError(e);
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
