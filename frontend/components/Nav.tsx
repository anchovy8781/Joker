"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";
import { useAuth } from "./AuthProvider";

const LINKS = [
  { href: "/", label: "대시보드" },
  { href: "/assets", label: "종목" },
  { href: "/portfolio", label: "내 자산" },
  { href: "/leaderboard", label: "랭킹" },
  { href: "/status", label: "시스템 상태" },
];

export function Nav() {
  const { user, logout } = useAuth();
  const path = usePathname();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const links = user?.role === "admin" ? [...LINKS, { href: "/admin", label: "관리자" }] : LINKS;
  const active = (href: string) => (href === "/" ? path === "/" : path.startsWith(href));

  return (
    <nav className="sticky top-0 z-20 border-b border-line bg-bg/95 backdrop-blur">
      <div className="mx-auto flex max-w-7xl items-center gap-4 px-4 py-3">
        <Link href="/" className="flex items-center gap-2 font-bold text-text">
          <span className="rounded bg-red-600 px-1.5 py-0.5 text-xs text-white" aria-hidden>▶</span>
          YouTube Exchange
        </Link>
        <button className="ml-auto rounded border border-line px-2 py-1 text-sm md:hidden" onClick={() => setOpen(!open)}
          aria-expanded={open} aria-label="메뉴">☰</button>
        <div className={`${open ? "flex" : "hidden"} absolute left-0 right-0 top-full flex-col gap-1 border-b border-line bg-bg p-3 md:static md:flex md:flex-1 md:flex-row md:items-center md:border-0 md:p-0`}>
          {links.map((l) => (
            <Link key={l.href} href={l.href} onClick={() => setOpen(false)}
              className={`rounded px-3 py-1.5 text-sm ${active(l.href) ? "bg-panel2 text-text" : "text-muted hover:text-text"}`}>
              {l.label}
            </Link>
          ))}
          <div className="md:ml-auto flex items-center gap-2 pt-2 md:pt-0">
            {user ? (
              <>
                <span className="text-sm text-muted">👤 {user.username}</span>
                <button className="btn-ghost" onClick={async () => { await logout(); router.push("/"); }}>로그아웃</button>
              </>
            ) : (
              <>
                <Link href="/login" className="btn-ghost">로그인</Link>
                <Link href="/register" className="btn-primary">회원가입</Link>
              </>
            )}
          </div>
        </div>
      </div>
    </nav>
  );
}
