"use client";

import { useCallback, useEffect, useState } from "react";

const KEY = "ytx.favorites";

function read(): number[] {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? "[]");
    return Array.isArray(v) ? v.filter((x) => typeof x === "number") : [];
  } catch {
    return [];
  }
}

/** Favourite stocks, kept on this device only. */
export function useFavorites() {
  const [ids, setIds] = useState<number[]>([]);
  useEffect(() => setIds(read()), []);
  const toggle = useCallback((id: number) => {
    setIds((cur) => {
      const next = cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id];
      try { localStorage.setItem(KEY, JSON.stringify(next)); } catch { /* storage unavailable */ }
      return next;
    });
  }, []);
  return { ids, has: (id: number) => ids.includes(id), toggle };
}
