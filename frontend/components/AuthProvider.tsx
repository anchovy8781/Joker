"use client";

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, type User } from "@/lib/api";
import { cached, clearApiCache, prefetch, remember } from "@/lib/hooks";

type AuthState = { user: User | null; ready: boolean; refresh: () => Promise<void>; logout: () => Promise<void> };
const Ctx = createContext<AuthState>({ user: null, ready: false, refresh: async () => {}, logout: async () => {} });

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const me = await api<User | null>("/api/auth/me");
      const before = cached<User | null>("/api/auth/me");
      if ((before?.id ?? null) !== (me?.id ?? null)) clearApiCache(); // someone else signed in/out
      remember("/api/auth/me", me);
      setUser(me);
      if (me) prefetch(["/api/assets", "/api/account", "/api/account/positions"]);
    } catch {
      setUser(null);
    } finally {
      setReady(true);
    }
  }, []);

  // Show the last signed-in state immediately on launch; refresh() confirms it.
  useEffect(() => {
    const last = cached<User | null>("/api/auth/me");
    if (last) { setUser(last); setReady(true); }
  }, []);

  const logout = useCallback(async () => {
    try {
      await api("/api/auth/logout", { method: "POST" });
    } finally {
      clearApiCache();
      setUser(null);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return <Ctx.Provider value={{ user, ready, refresh, logout }}>{children}</Ctx.Provider>;
}

export const useAuth = () => useContext(Ctx);
