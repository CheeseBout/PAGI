import { ArrowDown, RotateCcw, Search } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { Approval, ChatMessage } from "../api/client";
import ApprovalCard, { type ApprovalDecision } from "./ApprovalCard";
import MessageBubble from "./MessageBubble";
import { SubAgentTrace, type SubAgentEntry } from "./SubAgentTrace";
import { Button } from "@/components/ui/button";
import { ErrorBanner } from "@/components/ui/error-banner";

export interface ToolEvent {
  tool_call_id: string;
  tool_name: string;
  status: "running" | "done";
  result?: unknown;
}

// How close to the bottom (px) counts as "already there" — inside this, new
// content auto-scrolls the view; outside it, the user is reading history and
// a "Jump to latest" button appears instead of yanking their scroll position.
const NEAR_BOTTOM_PX = 120;

function MessageSkeleton() {
  return (
    <div className="flex flex-col gap-4 px-0 py-2" aria-hidden="true">
      {[0, 1, 2].map((i) => (
        <div key={i} className="msg mx-auto w-full max-w-[780px] animate-pulse">
          <div className="mb-1 h-2.5 w-14 rounded bg-bg-alt" />
          <div className="h-12 rounded-lg border border-border bg-bg-alt" />
        </div>
      ))}
    </div>
  );
}

export default function ChatWindow({
  messages,
  loading,
  loadError,
  onRetryLoad,
  streamingText,
  streamingRag,
  toolEvents,
  subAgents,
  patternLines,
  approvals,
  busy,
  hasMore,
  onDecision,
  onLoadOlder,
  onEditMessage,
  onRegenerate,
}: {
  messages: ChatMessage[];
  loading?: boolean;
  loadError?: string | null;
  onRetryLoad?: () => void;
  streamingText: string | null;
  streamingRag?: { count: number; ms: number; no_context?: boolean; reason?: string } | null;
  toolEvents: ToolEvent[];
  subAgents: SubAgentEntry[];
  patternLines: string[];
  approvals: Approval[];
  busy: boolean;
  hasMore: boolean;
  onDecision: (approvalId: string, decision: "approve" | "deny", remember?: "session") => void;
  onLoadOlder: () => void;
  onEditMessage: (messageId: string, content: string) => void;
  onRegenerate: () => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [pinnedToBottom, setPinnedToBottom] = useState(true);

  function isNearBottom(el: HTMLDivElement) {
    return el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX;
  }

  useEffect(() => {
    const el = scrollRef.current;
    if (!el || !pinnedToBottom) return;
    el.scrollTop = el.scrollHeight;
  }, [messages, streamingText, toolEvents, subAgents, patternLines, approvals, pinnedToBottom]);

  const lastMessage = messages[messages.length - 1];
  const canRegenerate =
    !busy && streamingText === null && lastMessage && lastMessage.role === "assistant";

  return (
    <div className="relative flex flex-1 flex-col min-h-0">
      <div
        ref={scrollRef}
        className="chat-window flex flex-1 flex-col gap-4 overflow-y-auto px-3 py-4 sm:px-4 sm:py-5"
        onScroll={(e) => setPinnedToBottom(isNearBottom(e.currentTarget))}
      >
        {hasMore && (
          <Button
            variant="ghost"
            className="load-older self-center text-2xs text-muted"
            onClick={onLoadOlder}
          >
            Load earlier messages
          </Button>
        )}

        {loadError ? (
          <div className="mt-10 flex flex-col items-center gap-2 text-center">
            <ErrorBanner message={loadError} />
            {onRetryLoad && (
              <Button variant="outline" size="sm" onClick={onRetryLoad}>
                Retry
              </Button>
            )}
          </div>
        ) : loading && messages.length === 0 ? (
          <MessageSkeleton />
        ) : (
          messages.length === 0 &&
          !streamingText && (
            <div className="empty-hint mt-10 text-center text-muted">Start the conversation below.</div>
          )
        )}

        {messages.map((m) => (
          <MessageBubble key={m.id} message={m} onEdit={onEditMessage} editDisabled={busy} />
        ))}

        <div aria-live="polite" className="contents">
          {toolEvents.map((t) => (
            <div key={t.tool_call_id} className="tool-event flex items-center gap-2 self-center text-sm text-muted">
              <span
                className={`spinner size-2.5 rounded-full border-2 ${
                  t.status === "done"
                    ? "border-accent"
                    : "animate-spin border-muted border-t-transparent"
                }`}
              />
              {t.status === "running" ? `Running ${t.tool_name}…` : `${t.tool_name} finished`}
            </div>
          ))}
        </div>

        {subAgents.map((s) => (
          <SubAgentTrace key={s.subSessionId} entry={s} />
        ))}

        {patternLines.length > 0 && (
          <div className="pattern-activity mx-auto flex w-full max-w-[780px] flex-col gap-px self-center rounded-lg border border-orchestra/30 bg-orchestra/5 px-2.5 py-1.5 font-mono text-2xs text-muted">
            {patternLines.map((l, i) => (
              <div key={i}>{l}</div>
            ))}
          </div>
        )}

        {approvals.map((a) => (
          <ApprovalCard
            key={a.id}
            approval={a}
            onDecision={((d, remember) => onDecision(a.id, d, remember)) as ApprovalDecision}
          />
        ))}

        {streamingText !== null && (
          <div
            className="msg msg-assistant mx-auto flex w-full max-w-[780px] flex-col items-start gap-1 self-center"
            aria-live="polite"
            aria-atomic="false"
          >
            <span className="sr-only">assistant</span>
            {streamingRag && (
              <div className="rag-inline flex items-center gap-1.5 text-xs text-muted">
                <Search className="size-3 shrink-0" />
                {streamingRag.no_context
                  ? `nothing relevant in the knowledge base${
                      streamingRag.reason ? ` (${streamingRag.reason})` : ""
                    }`
                  : `retrieved ${streamingRag.count} passage(s) · ${streamingRag.ms} ms`}
              </div>
            )}
            <div className="msg-body streaming w-full whitespace-pre-wrap">{streamingText || "…"}</div>
          </div>
        )}

        {canRegenerate && (
          <Button
            variant="outline"
            size="sm"
            className="regenerate-btn self-center text-2xs"
            onClick={onRegenerate}
          >
            <RotateCcw className="size-3.5" /> Regenerate
          </Button>
        )}
      </div>

      {!pinnedToBottom && (
        <Button
          variant="outline"
          size="sm"
          className="absolute bottom-4 left-1/2 -translate-x-1/2 rounded-full bg-bg-elev shadow-md"
          onClick={() => {
            setPinnedToBottom(true);
            const el = scrollRef.current;
            if (el) el.scrollTop = el.scrollHeight;
          }}
        >
          <ArrowDown className="size-3.5" /> Jump to latest
        </Button>
      )}
    </div>
  );
}
