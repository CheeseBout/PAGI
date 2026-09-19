// The speech bubble above the avatar (SPEC §21.4.2). Purely presentational:
// what to show comes from features/overlay/bubbleRules.ts.
import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Approval } from "@/api/client";
import type { Bubble } from "@/features/overlay/bubbleRules";
import { HIT_ATTR } from "@/features/overlay/hitTest";
import { cn } from "@/lib/utils";

const hit = { [HIT_ATTR]: "" };

const RISKY = new Set(["execute_code", "delete_file"]);
const ARGS_PREVIEW_CHARS = 300;

function argsText(approval: Approval): string {
  const args = { ...(approval.tool_args || {}) } as Record<string, unknown>;
  delete args._preview; // the diff preview is web-UI material, too big for a bubble
  return JSON.stringify(args, null, 2);
}

function ApprovalBody({
  approval,
  onDecision,
}: {
  approval: Approval;
  onDecision: (approvalId: string, decision: "approve" | "deny") => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const full = argsText(approval);
  const shown = expanded || full.length <= ARGS_PREVIEW_CHARS ? full : full.slice(0, ARGS_PREVIEW_CHARS) + "…";
  const risky = RISKY.has(approval.tool_name);

  return (
    <div className="flex flex-col gap-2" role="alertdialog" aria-label="Approval required">
      <div className="flex items-center gap-2 text-xs">
        <span
          className={cn(
            "rounded px-1.5 py-0.5 font-medium",
            risky ? "bg-danger text-white" : "bg-bg-alt text-muted",
          )}
        >
          {risky ? "Hành động không hoàn tác" : "Cần bạn duyệt"}
        </span>
        <span className="font-mono">{approval.tool_name}</span>
      </div>
      <pre className="max-h-16 overflow-auto whitespace-pre-wrap break-words rounded bg-code-bg p-2 text-xs">
        {shown}
      </pre>
      {full.length > ARGS_PREVIEW_CHARS && (
        <button className="self-start text-xs text-muted underline" onClick={() => setExpanded((v) => !v)}>
          {expanded ? "Thu gọn" : "Xem đầy đủ"}
        </button>
      )}
      <div className="flex gap-2">
        <button
          className="rounded-md bg-accent px-3 py-1 text-sm text-accent-fg"
          onClick={() => onDecision(approval.id, "approve")}
        >
          Duyệt
        </button>
        <button
          className="rounded-md border border-border px-3 py-1 text-sm"
          onClick={() => onDecision(approval.id, "deny")}
        >
          Từ chối
        </button>
      </div>
    </div>
  );
}

export default function OverlayBubble({
  bubble,
  onOpenPanel,
  onDecision,
  onHover,
}: {
  bubble: Bubble;
  onOpenPanel: () => void;
  onDecision: (approvalId: string, decision: "approve" | "deny") => void;
  onHover: (hovering: boolean) => void;
}) {
  const clickable = bubble.kind === "text";
  return (
    <div
      {...hit}
      onMouseEnter={() => onHover(true)}
      onMouseLeave={() => onHover(false)}
      onClick={clickable ? onOpenPanel : undefined}
      className={cn(
        "max-h-full overflow-y-auto rounded-2xl border bg-bg-elev p-3 text-sm text-text shadow-lg",
        bubble.kind === "error" ? "border-danger" : "border-border",
        clickable && "cursor-pointer",
      )}
    >
      {bubble.kind === "approval" && <ApprovalBody approval={bubble.approval} onDecision={onDecision} />}

      {bubble.kind === "error" && <span className="text-danger">{bubble.message}</span>}

      {bubble.kind === "tool" && <span className="text-muted">{bubble.label}</span>}

      {bubble.kind === "text" && (
        <>
          {bubble.text.truncated ? (
            <p className="m-0 whitespace-pre-wrap break-words">{bubble.text.text}</p>
          ) : (
            <div className="bubble-md break-words">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{bubble.text.text}</ReactMarkdown>
            </div>
          )}
          {bubble.streaming && <span className="ml-0.5 inline-block animate-pulse">▍</span>}
          {bubble.text.truncated && (
            <div className="mt-1 text-xs text-muted">… bấm để xem đầy đủ</div>
          )}
        </>
      )}
    </div>
  );
}
