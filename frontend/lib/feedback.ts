"use client";

import { useEffect, useRef, useState } from "react";

export type ToastTone = "info" | "up" | "down" | "warn";

/** Show a short message at the bottom of the screen (see components/Toaster). */
export function toast(text: string, tone: ToastTone = "info") {
  if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent("ytx-toast", { detail: { text, tone } }));
}

/** A small buzz on phones that support it (the Android app does); silently skipped elsewhere. */
export function buzz(pattern: number | number[] = 25) {
  try { if (typeof navigator !== "undefined" && "vibrate" in navigator) navigator.vibrate(pattern); } catch { /* ignore */ }
}

/** Briefly tint a number red/blue when it goes up/down between renders, like a trading screen. */
export function usePriceFlash(value: number | undefined | null): string {
  const prev = useRef(value);
  const [cls, setCls] = useState("");
  useEffect(() => {
    if (value == null || prev.current == null || value === prev.current) { prev.current = value; return; }
    setCls(value > prev.current ? "flash-up" : "flash-down");
    prev.current = value;
    const t = setTimeout(() => setCls(""), 700);
    return () => clearTimeout(t);
  }, [value]);
  return cls;
}
