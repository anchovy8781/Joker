"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "./AuthProvider";

export function AuthForm({ mode }: { mode: "login" | "register" }) {
  const router = useRouter();
  const { refresh } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const isRegister = mode === "register";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (isRegister && !/^[A-Za-z0-9_]{3,20}$/.test(username)) return setError("아이디는 영문/숫자/_ 3~20자입니다.");
    if (isRegister && password.length < 10) return setError("비밀번호는 10자 이상이어야 합니다.");
    setPending(true);
    try {
      await api(`/api/auth/${mode}`, { method: "POST", body: { username, password } });
      await refresh();
      router.push("/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "요청에 실패했습니다.");
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="mx-auto max-w-sm">
      <h1 className="mb-1 text-xl font-bold">{isRegister ? "회원가입" : "로그인"}</h1>
      {isRegister && (
        <p className="mb-4 text-sm text-muted">
          가입 시 1회에 한해 게임 크레딧 1,000,000이 지급됩니다. 크레딧은 현금 가치가 없으며 구매·환전할 수 없습니다.
        </p>
      )}
      <form onSubmit={submit} className="space-y-3 rounded-xl border border-line bg-panel p-4">
        <label className="block text-xs text-muted">아이디
          <input className="input mt-1" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} name="username" />
        </label>
        <label className="block text-xs text-muted">비밀번호
          <input className="input mt-1" type="password" autoComplete={isRegister ? "new-password" : "current-password"}
            value={password} onChange={(e) => setPassword(e.target.value)} name="password" />
        </label>
        {error && <div role="alert" className="text-sm text-red-300">⚠ {error}</div>}
        <button type="submit" className="btn-primary w-full py-2" disabled={pending}>
          {pending ? "처리 중…" : isRegister ? "가입하기" : "로그인"}
        </button>
      </form>
      <p className="mt-3 text-center text-sm text-muted">
        {isRegister ? <>이미 계정이 있나요? <Link className="text-text underline" href="/login">로그인</Link></>
          : <>처음이신가요? <Link className="text-text underline" href="/register">회원가입</Link></>}
      </p>
    </div>
  );
}
