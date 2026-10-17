import type { Metadata, Viewport } from "next";
import { AuthProvider } from "@/components/AuthProvider";
import { Nav } from "@/components/Nav";
import "./globals.css";

export const metadata: Metadata = {
  title: "YouTube Exchange — 가상 채널 주식 거래소",
  description: "유튜브 채널을 가상 종목으로 거래하는 게임. 모든 거래는 현금 가치가 없는 게임 크레딧으로만 이루어집니다.",
};
export const viewport: Viewport = { themeColor: "#0b0e13", width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body className="min-h-screen">
        <AuthProvider>
          <Nav />
          <main className="mx-auto max-w-7xl px-4 py-6">{children}</main>
          <footer className="mx-auto max-w-7xl px-4 pb-10 pt-4 text-xs leading-relaxed text-muted">
            모든 가격은 게임 규칙에 따른 가상 가격이며 실제 채널의 금전적 가치나 미래 성과를 의미하지 않습니다.
            크레딧은 현금 가치가 없고 구매·환전·출금할 수 없습니다. 이 서비스는 YouTube와 제휴하지 않았습니다.
            <span className="ml-1 rounded border border-accent/60 px-1 text-accent">DEMO</span> 표시 종목의 지표는 데모 공급자가 생성한 가상 데이터입니다.
          </footer>
        </AuthProvider>
      </body>
    </html>
  );
}
