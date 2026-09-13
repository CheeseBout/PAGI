import { AlertOctagon, FilePenLine, ShieldQuestion } from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import type { Approval } from "../api/client";
import { diffStats, lineDiff } from "../lib/linediff";
import { checkboxCls } from "./settings/formStyles";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const CODE_TOOLS = new Set(["execute_code"]);

// Risk tier drives every visual signal on this card (stripe, badge, icon) —
// it is read from the tool name because that's the one thing every approval
// carries, and it's the thing the person deciding actually needs to weigh.
// Irreversible / trust-critical actions must never share a color with a
// merely-notable one (SPEC audit finding — the old card used --accent for
// every tool, `execute_code` and `delete_file` included).
type Tier = "risk" | "warn" | "neutral";

const RISK_TOOLS = new Set(["execute_code", "delete_file"]);
const WARN_TOOLS = new Set(["write_file", "edit_file"]);

function tierOf(toolName: string): Tier {
  if (RISK_TOOLS.has(toolName)) return "risk";
  if (WARN_TOOLS.has(toolName)) return "warn";
  return "neutral";
}

const TIER_META: Record<
  Tier,
  { label: string; icon: ReactNode; border: string; badge: string; ring: string }
> = {
  risk: {
    label: "Irreversible action",
    icon: <AlertOctagon className="size-3.5" />,
    border: "border-l-danger",
    badge: "bg-danger text-white",
    ring: "border-danger/40",
  },
  warn: {
    label: "File change",
    icon: <FilePenLine className="size-3.5" />,
    border: "border-l-warn",
    badge: "bg-warn-subtle text-warn-subtle-fg",
    ring: "border-warn/40",
  },
  neutral: {
    label: "Needs approval",
    icon: <ShieldQuestion className="size-3.5" />,
    border: "border-l-accent",
    badge: "bg-bg-alt text-muted",
    ring: "border-border",
  },
};

export type ApprovalDecision = (decision: "approve" | "deny", remember?: "session") => void;

// Purely a visual hint — aria-hidden so it never becomes part of the
// button's accessible name (a screen reader, and Playwright's `getByRole`
// name matching, both compute the name from visible text otherwise).
// Inherits currentColor so it reads on both the filled Approve button and
// the outline Deny button, in either theme.
function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd aria-hidden="true" className="ml-1 font-mono text-3xs opacity-60">
      ({children})
    </kbd>
  );
}

export default function ApprovalCard({
  approval,
  onDecision,
}: {
  approval: Approval;
  onDecision: ApprovalDecision;
}) {
  const args = (approval.tool_args || {}) as Record<string, unknown>;
  const preview = approval.tool_args?._preview;
  const codeLike = CODE_TOOLS.has(approval.tool_name) && typeof args.code === "string";
  const [showRaw, setShowRaw] = useState(false);
  const [alwaysAllow, setAlwaysAllow] = useState(false);
  const cardRef = useRef<HTMLDivElement>(null);

  const tier = tierOf(approval.tool_name);
  const meta = TIER_META[tier];

  // Focus the card the moment it appears, and let A/D decide it — this is a
  // gate on real side effects, so it should be usable without reaching for
  // the mouse. Scoped to when the card itself holds focus, so it never
  // steals A/D from the message composer or anywhere else.
  useEffect(() => {
    cardRef.current?.focus();
  }, []);

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (e.target !== e.currentTarget) return;
    if (e.key === "a" || e.key === "A") {
      e.preventDefault();
      onDecision("approve", alwaysAllow ? "session" : undefined);
    } else if (e.key === "d" || e.key === "D") {
      e.preventDefault();
      onDecision("deny");
    }
  }

  const argsForDisplay = { ...args };
  delete (argsForDisplay as Record<string, unknown>)._preview;

  return (
    <div
      ref={cardRef}
      tabIndex={-1}
      role="alertdialog"
      aria-label={`${meta.label}: ${approval.tool_name} needs approval`}
      onKeyDown={onKeyDown}
      className={cn(
        "approval-card mx-auto w-full max-w-[780px] self-center rounded-md border border-l-4 bg-bg-elev p-3.5 focus:outline-none",
        meta.ring,
        meta.border,
      )}
    >
      <div className="approval-head mb-2.5 flex items-center gap-2.5">
        <span
          className={cn(
            "approval-badge inline-flex items-center gap-1.5 rounded-full px-2 py-[3px] text-3xs font-semibold",
            meta.badge,
          )}
        >
          {meta.icon}
          {meta.label}
        </span>
        {approval.agent_name && (
          <span
            className="approval-agent rounded-full bg-orchestra px-2 py-0.5 text-3xs font-semibold text-white"
            title="Requested by a sub-agent"
          >
            {approval.agent_name}
          </span>
        )}
        <code className="text-2xs">{approval.tool_name}</code>
        {preview && (
          <Button
            variant="ghost"
            size="sm"
            className="ml-auto text-muted"
            onClick={() => setShowRaw((v) => !v)}
          >
            {showRaw ? "Show diff" : "Show raw args"}
          </Button>
        )}
      </div>

      {preview && !showRaw ? (
        <DiffView preview={preview} />
      ) : codeLike ? (
        <pre className="approval-code overflow-x-auto rounded-lg bg-code-bg p-3 text-2xs text-code-fg">
          <code>{String(args.code)}</code>
        </pre>
      ) : (
        <pre className="approval-code overflow-x-auto rounded-lg bg-code-bg p-3 text-2xs text-code-fg">
          <code>{JSON.stringify(argsForDisplay, null, 2)}</code>
        </pre>
      )}

      <div className="approval-actions mt-3 flex flex-wrap items-center gap-x-3 gap-y-2">
        <Button onClick={() => onDecision("approve", alwaysAllow ? "session" : undefined)}>
          Approve <Kbd>A</Kbd>
        </Button>
        <Button variant="outline" onClick={() => onDecision("deny")}>
          Deny <Kbd>D</Kbd>
        </Button>
        <label className="ml-auto flex cursor-pointer items-center gap-1.5 text-2xs text-muted">
          <input
            type="checkbox"
            className={checkboxCls}
            checked={alwaysAllow}
            onChange={(e) => setAlwaysAllow(e.target.checked)}
          />
          Always allow {approval.tool_name} this chat
        </label>
      </div>
    </div>
  );
}

function DiffView({ preview }: { preview: NonNullable<Approval["tool_args"]["_preview"]> }) {
  const rows = lineDiff(preview.old, preview.new);
  const { added, removed } = diffStats(rows);
  const isNew = preview.old === null;
  return (
    <div className="approval-diff overflow-hidden rounded-lg border border-border">
      <div className="approval-diff-head flex items-center justify-between gap-2.5 bg-bg-alt px-2.5 py-1.5 text-sm">
        <code>{preview.path}</code>
        <span className="text-muted">
          {isNew ? "new file · " : ""}
          <span className="diff-add-count text-diff-add">+{added}</span>{" "}
          <span className="diff-del-count text-diff-del">−{removed}</span>
          {preview.truncated ? " · truncated" : ""}
        </span>
      </div>
      <pre className="approval-diff-body m-0 max-h-[340px] overflow-y-auto overflow-x-auto bg-code-bg py-2 text-2xs text-code-fg">
        {rows.map((r, i) => (
          <div
            key={i}
            className={`diff-row diff-${r.kind} whitespace-pre px-2.5 font-mono ${
              r.kind === "add" ? "bg-diff-add-bg" : r.kind === "del" ? "bg-diff-del-bg" : ""
            }`}
          >
            <span className="diff-gutter mr-2 inline-block w-[1ch] opacity-60">
              {r.kind === "add" ? "+" : r.kind === "del" ? "−" : " "}
            </span>
            {r.text || " "}
          </div>
        ))}
      </pre>
    </div>
  );
}
