import { Cog, FileText, Info, Pencil } from "lucide-react";
import { memo, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";
import { api, type ChatMessage } from "../api/client";
import CitationList from "./CitationList";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

function CodeBlock({ className, children }: { className?: string; children?: ReactNode }) {
  const [copied, setCopied] = useState(false);
  const text = String(children ?? "");
  const isBlock = (className || "").includes("language-");
  if (!isBlock) return <code className={className}>{children}</code>;
  return (
    <div className="codeblock relative">
      <button
        className="copy-btn absolute right-1.5 top-1.5 rounded-md bg-bg-elev/80 px-2 py-0.5 text-xs text-text"
        onClick={() => {
          navigator.clipboard.writeText(text.replace(/\n$/, ""));
          setCopied(true);
          setTimeout(() => setCopied(false), 1200);
        }}
      >
        {copied ? "Copied" : "Copy"}
      </button>
      <pre className="rounded-lg bg-code-bg p-3 overflow-x-auto">
        <code className={className}>{children}</code>
      </pre>
    </div>
  );
}

/** Memoized — the chat window re-renders on every streamed token, and without
 * this every historical bubble (markdown parse + syntax highlight included)
 * would redo that work each time too. */
const MessageBubble = memo(function MessageBubble({
  message,
  onEdit,
  editDisabled,
}: {
  message: ChatMessage;
  /** Only called for role="user" messages — PLAN §7.8 "sửa tin nhắn đã gửi". */
  onEdit?: (messageId: string, newContent: string) => void;
  editDisabled?: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(message.content ?? "");

  if (message.role === "tool") {
    let parsed: unknown = message.content;
    try {
      parsed = JSON.parse(message.content || "null");
    } catch {
      /* keep raw */
    }
    return (
      <div className="msg msg-tool mx-auto w-full max-w-[780px] self-center">
        <div className="msg-role mb-1 text-3xs uppercase tracking-wide text-muted">tool result</div>
        <pre className="tool-result overflow-x-auto rounded-lg bg-tool-bubble p-2.5 text-xs">
          {JSON.stringify(parsed, null, 2)}
        </pre>
      </div>
    );
  }

  const hasToolCalls = message.tool_calls && message.tool_calls.length > 0;
  const canEdit = message.role === "user" && !!onEdit && !message.id.startsWith("local-");
  const attachments = message.attachments ?? [];
  const sid = message.session_id;
  const isUser = message.role === "user";

  function save() {
    const trimmed = draft.trim();
    if (trimmed && trimmed !== message.content) onEdit?.(message.id, trimmed);
    setEditing(false);
  }

  return (
    <div
      className={cn(
        "msg group/msg mx-auto flex w-full max-w-[780px] flex-col gap-1 self-center",
        `msg-${message.role}`,
        isUser ? "items-end" : "items-start",
      )}
    >
      <span className="sr-only">{message.role}</span>

      {editing ? (
        <div className="edit-box flex w-full flex-col gap-2">
          <Textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={3} autoFocus />
          <div className="edit-actions flex gap-2">
            <Button onClick={save}>Save & resend</Button>
            <Button
              variant="outline"
              onClick={() => {
                setDraft(message.content ?? "");
                setEditing(false);
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <>
          {attachments.length > 0 && sid && (
            <div className={cn("msg-attachments flex flex-wrap gap-2", isUser && "justify-end")}>
              {attachments.map((a) =>
                a.kind === "image" ? (
                  <a key={a.id} href={api.fileUrl(sid, a.id)} target="_blank" rel="noreferrer">
                    <img
                      className="msg-attachment-img block max-h-[260px] max-w-[260px] rounded-lg border border-border"
                      src={api.fileUrl(sid, a.id)}
                      alt={a.filename}
                    />
                  </a>
                ) : (
                  <a
                    key={a.id}
                    className="attachment-chip inline-flex max-w-[260px] items-center gap-1.5 overflow-hidden text-ellipsis whitespace-nowrap rounded-full border border-border px-2 py-0.5 text-xs text-text no-underline"
                    href={api.fileUrl(sid, a.id)}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <FileText className="size-3.5 shrink-0" /> {a.filename}
                    {a.workspace_path ? " · in workspace" : ""}
                  </a>
                ),
              )}
            </div>
          )}
          {message.content &&
            (isUser ? (
              <div className="msg-body max-w-[75%] rounded-md border border-border bg-user-bubble px-3.5 py-1.5 [&_p]:my-2.5 [&_pre]:m-0">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  rehypePlugins={[rehypeHighlight]}
                  components={{ code: CodeBlock as never }}
                >
                  {message.content}
                </ReactMarkdown>
              </div>
            ) : (
              <div className="msg-body w-full [&_p]:my-2.5 [&_pre]:m-0">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  rehypePlugins={[rehypeHighlight]}
                  components={{ code: CodeBlock as never }}
                >
                  {message.content}
                </ReactMarkdown>
              </div>
            ))}
          {hasToolCalls && (
            <div className="tool-calls flex flex-col gap-1">
              {message.tool_calls!.map((tc) => (
                <div key={tc.id} className="tool-call-chip flex items-center gap-2 text-xs text-muted">
                  <Cog className="size-3.5 shrink-0" /> {tc.name}
                  <code>{JSON.stringify(tc.args)}</code>
                </div>
              ))}
            </div>
          )}
          {message.role === "assistant" && message.rag && <CitationList rag={message.rag} />}

          {(canEdit || (message.role === "assistant" && hasStats(message))) && (
            <div className="flex items-center gap-2 opacity-0 transition-opacity focus-within:opacity-100 group-hover/msg:opacity-100">
              {canEdit && !editing && (
                <button
                  className="edit-btn inline-flex items-center gap-1 text-3xs text-muted [&:not(:disabled):hover]:text-accent disabled:opacity-50"
                  disabled={editDisabled}
                  onClick={() => setEditing(true)}
                  title="Edit & resend"
                >
                  <Pencil className="size-3" /> edit
                </button>
              )}
              {message.role === "assistant" && hasStats(message) && <MessageStats message={message} />}
            </div>
          )}
        </>
      )}
    </div>
  );
});

function hasStats(message: ChatMessage) {
  return (message.tokens_in != null && message.tokens_out != null) || message.cost_usd != null || message.latency_ms != null;
}

/** tokens/cost/latency for this turn — previously always shown inline under
 * every reply; now tucked behind a hover-revealed info icon (§Phase 3) so a
 * long conversation reads as prose, not a log of numbers. */
function MessageStats({ message }: { message: ChatMessage }) {
  const hasTokens = message.tokens_in != null && message.tokens_out != null;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex cursor-default items-center text-muted hover:text-text">
          <Info className="size-3" />
        </span>
      </TooltipTrigger>
      <TooltipContent className="flex flex-col gap-0.5">
        {hasTokens && (
          <span>
            {message.tokens_in} → {message.tokens_out} tok
          </span>
        )}
        {message.cost_usd != null && <span>${message.cost_usd.toFixed(4)}</span>}
        {message.latency_ms != null && <span>{(message.latency_ms / 1000).toFixed(1)}s</span>}
      </TooltipContent>
    </Tooltip>
  );
}

export default MessageBubble;
