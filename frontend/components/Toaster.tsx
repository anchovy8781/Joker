"use client";

import { useEffect, useState } from "react";
import type { ToastTone } from "@/lib/feedback";

type Item = { id: number; text: string; tone: ToastTone };

const TONE: Record<ToastTone, string> = {
  info: "bg-panel2 text-text", up: "bg-up text-white", down: "bg-down text-white", warn: "bg-amber-500 text-black",
};

/** Bottom-of-screen messages (orders filled, rewards…), above the tab bar. */
export function Toaster() {
  const [items, setItems] = useState<Item[]>([]);
  useEffect(() => {
    let n = 0;
    const on = (e: Event) => {
      const { text, tone } = (e as CustomEvent<{ text: string; tone: ToastTone }>).detail;
      const id = ++n;
      setItems((xs) => [...xs.slice(-2), { id, text, tone }]);
      setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), 3200);
    };
    window.addEventListener("ytx-toast", on);
    return () => window.removeEventListener("ytx-toast", on);
  }, []);
  if (!items.length) return null;
  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-24 z-50 flex flex-col items-center gap-2 px-4 lg:bottom-8" aria-live="polite">
      {items.map((t) => (
        <div key={t.id} className={`toast-in pointer-events-auto max-w-md rounded-2xl px-4 py-3 text-sm font-semibold shadow-2xl ${TONE[t.tone]}`}>
          {t.text}
        </div>
      ))}
    </div>
  );
}
