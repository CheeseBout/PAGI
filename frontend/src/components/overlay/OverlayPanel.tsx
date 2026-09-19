// The expanded conversation panel (SPEC §21.4.3): recent messages of the one
// continuous session, an agent switcher, and a link to the full history in the
// web UI. Shows at most PANEL_MAX_MESSAGES; older history lives on the web.
import { ExternalLink, X } from "lucide-react";
import { useEffect, useMemo, useRef } from "react";
import type { Agent, ChatMessage } from "@/api/client";
import MessageBubble from "@/components/MessageBubble";
import { HIT_ATTR } from "@/features/overlay/hitTest";

const hit = { [HIT_ATTR]: "" };
export const PANEL_MAX_MESSAGES = 50;

export default function OverlayPanel({
  messages,
  streamingText,
  agents,
  agentId,
  onSelectAgent,
  onOpenHistory,
  onClose,
}: {
  messages: ChatMessage[];
  streamingText: string | null;
  agents: Agent[];
  agentId: string | null;
  onSelectAgent: (id: string) => void;
  onOpenHistory: () => void;
  onClose: () => void;
}) {
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // user/assistant turns only — tool rows and empty tool-call stubs are noise here
  const shown = useMemo(
    () =>
      messages
        .filter((m) => (m.role === "user" || m.role === "assistant") && (m.content || "").trim())
        .slice(-PANEL_MAX_MESSAGES),
    [messages],
  );

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [shown.length, streamingText]);

  const live: ChatMessage | null = streamingText
    ? { id: "streaming", role: "assistant", content: streamingText, created_at: "" }
    : null;

  return (
    <section
      {...hit}
      className="ml-2 flex h-full min-w-0 flex-1 flex-col overflow-hidden rounded-2xl border border-border bg-bg-elev shadow-lg"
    >
      <header className="flex items-center gap-2 border-b border-border px-3 py-2 text-sm">
        <select
          value={agentId ?? ""}
          onChange={(e) => onSelectAgent(e.target.value)}
          className="min-w-0 flex-1 truncate rounded border border-border bg-bg px-1 py-0.5"
          aria-label="Agent"
        >
          {agents.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </select>
        <button
          onClick={onOpenHistory}
          title="Mở lịch sử đầy đủ trên web"
          aria-label="Mở lịch sử đầy đủ"
          className="grid size-7 place-items-center rounded text-muted hover:bg-bg-alt"
        >
          <ExternalLink className="size-4" />
        </button>
        <button
          onClick={onClose}
          title="Thu gọn (Esc)"
          aria-label="Thu gọn"
          className="grid size-7 place-items-center rounded text-muted hover:bg-bg-alt"
        >
          <X className="size-4" />
        </button>
      </header>

      <div ref={scrollRef} className="relative flex flex-1 flex-col gap-3 overflow-y-auto p-3 select-text">
        {shown.length === 0 && !live && (
          <p className="m-auto text-center text-sm text-muted">Chưa có tin nhắn. Hãy hỏi gì đó.</p>
        )}
        {shown.map((m) => (
          <MessageBubble key={m.id} message={m} />
        ))}
        {live && <MessageBubble key={live.id} message={live} />}
      </div>
    </section>
  );
}
