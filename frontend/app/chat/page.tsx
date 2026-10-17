"use client";

import { useState } from "react";
import { ChatBox } from "@/components/ChatBox";
import { Card } from "@/components/ui";
import type { Asset } from "@/lib/api";
import { useApi } from "@/lib/hooks";

export default function ChatPage() {
  const [room, setRoom] = useState("global");
  const { data: assets } = useApi<Asset[]>("/api/assets");
  return (
    <Card title="💬 단체 채팅" right={
      <select className="input w-40 py-1 text-xs" value={room} onChange={(e) => setRoom(e.target.value)} aria-label="채팅방 선택">
        <option value="global">전체 채팅</option>
        {assets?.map((a) => <option key={a.id} value={`asset:${a.id}`}>{a.symbol} 종목방</option>)}
      </select>}>
      <ChatBox room={room} />
    </Card>
  );
}
