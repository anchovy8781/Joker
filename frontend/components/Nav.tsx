"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";
import { useAuth } from "./AuthProvider";
import { MarketHoursBadge } from "./MarketHoursBadge";
import { NotificationBell } from "./NotificationBell";

// Bottom tab bar on phones (thumb reach), top links on desktop.
const TABS = [
  { href: "/", label: "홈", icon: "🏠" },
  { href: "/assets", label: "종목", icon: "📈" },
  { href: "/rankings", label: "순위", icon: "🏆" },
  { href: "/chat", label: "채팅", icon: "💬" },
  { href: "/portfolio", label: "내 자산", icon: "💼" },
];
const MORE = [
  { href: "/missions", label: "미션", icon: "🎯" },
  { href: "/donations", label: "기부함", icon: "🎁" },
  { href: "/listings", label: "상장 요청", icon: "📝" },
  { href: "/leaderboard", label: "자산 랭킹", icon: "👑" },
  { href: "/status", label: "시스템 상태", icon: "🩺" },
];

export function Nav() {
  const { user, logout } = useAuth();
  const path = usePathname();
  const router = useRouter();
  const [more, setMore] = useState(false);
  const extra = user?.role === "admin" ? [...MORE, { href: "/admin", label: "관리자", icon: "🛡" }] : MORE;
  const active = (href: string) => (href === "/" ? path === "/" : path.startsWith(href));

  return (
    <>
      <nav className="sticky top-0 z-20 border-b border-line bg-bg/95 pt-[env(safe-area-inset-top)] backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center gap-2 px-4 py-2.5">
          <Link href="/" className="flex shrink-0 items-center gap-1.5 font-bold text-text">
            <span className="rounded bg-red-600 px-1.5 py-0.5 text-xs text-white" aria-hidden>▶</span>
            <span className="hidden sm:inline">YouTube Exchange</span>
            <span className="sm:hidden">YTX</span>
          </Link>
          <MarketHoursBadge />
          <div className="ml-2 hidden flex-1 items-center gap-1 lg:flex">
            {[...TABS, ...extra].map((l) => (
              <Link key={l.href} href={l.href}
                className={`rounded px-2.5 py-1.5 text-sm ${active(l.href) ? "bg-panel2 text-text" : "text-muted hover:text-text"}`}>
                {l.label}
              </Link>
            ))}
          </div>
          <div className="ml-auto flex items-center gap-2">
            {user ? (
              <>
                <NotificationBell />
                <button className="btn-ghost hidden lg:inline-block" onClick={async () => { await logout(); router.push("/"); }}>로그아웃</button>
              </>
            ) : (
              <>
                <Link href="/login" className="btn-ghost">로그인</Link>
                <Link href="/register" className="btn-primary">가입</Link>
              </>
            )}
            <button className="rounded-lg border border-line px-2.5 py-1.5 text-sm lg:hidden" onClick={() => setMore(true)}
              aria-label="더보기 메뉴" aria-expanded={more}>☰</button>
          </div>
        </div>
      </nav>

      {/* Bottom tab bar (mobile) */}
      <nav aria-label="주요 메뉴" className="fixed inset-x-0 bottom-0 z-20 border-t border-line bg-bg/95 pb-[env(safe-area-inset-bottom)] backdrop-blur lg:hidden">
        <div className="grid grid-cols-5">
          {TABS.map((t) => (
            <Link key={t.href} href={t.href}
              className={`flex flex-col items-center gap-0.5 py-2 text-[11px] ${active(t.href) ? "text-text" : "text-muted"}`}
              aria-current={active(t.href) ? "page" : undefined}>
              <span className="text-lg leading-none" aria-hidden>{t.icon}</span>
              {t.label}
            </Link>
          ))}
        </div>
      </nav>

      {/* "More" sheet */}
      {more && (
        <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-label="더보기">
          <button className="absolute inset-0 bg-black/60" aria-label="닫기" onClick={() => setMore(false)} />
          <div className="absolute inset-x-0 bottom-0 rounded-t-2xl border-t border-line bg-panel p-4 pb-[calc(1rem+env(safe-area-inset-bottom))]">
            <div className="mx-auto mb-3 h-1 w-10 rounded-full bg-line" />
            <div className="grid grid-cols-3 gap-2">
              {extra.map((l) => (
                <Link key={l.href} href={l.href} onClick={() => setMore(false)}
                  className="flex flex-col items-center gap-1 rounded-xl bg-panel2 py-3 text-sm">
                  <span className="text-xl" aria-hidden>{l.icon}</span>{l.label}
                </Link>
              ))}
            </div>
            {user && (
              <button className="btn-ghost mt-3 w-full py-2" onClick={async () => { setMore(false); await logout(); router.push("/"); }}>
                로그아웃 ({user.username})
              </button>
            )}
          </div>
        </div>
      )}
    </>
  );
}
