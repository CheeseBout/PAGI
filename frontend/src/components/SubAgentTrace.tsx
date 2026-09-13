// Collapsible card for a sub-agent's activity inside the chat (Phase 13c, SPEC §15).

import { CheckCircle2, ChevronDown, ChevronRight, CircleDot, XCircle } from "lucide-react";
import { useState } from "react";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";

export type SubAgentEntry = {
  subSessionId: string;
  agentName: string;
  depth: number;
  task: string;
  status: "running" | "ok" | "empty" | "denied";
  tokensIn?: number;
  tokensOut?: number;
  costUsd?: number;
  lines: string[]; // rolled-up event summaries
};

const STATUS_ICON = {
  running: <CircleDot className="size-3.5 animate-pulse text-orchestra" />,
  ok: <CheckCircle2 className="size-3.5 text-success" />,
  denied: <XCircle className="size-3.5 text-danger" />,
  empty: <span className="text-muted">·</span>,
};

export function SubAgentTrace({ entry }: { entry: SubAgentEntry }) {
  const [open, setOpen] = useState(false);
  // Real depth, not a "depth >= 2" flat cap — a root-delegated worker (depth 1)
  // sits flush; each further level of delegation indents another notch, so a
  // depth-4 sub-sub-sub-agent still reads as deeper than its depth-2 parent.
  const indent = Math.max(0, entry.depth - 1) * 20;
  return (
    <Collapsible
      open={open}
      onOpenChange={setOpen}
      style={{ marginLeft: indent }}
      className="subagent-trace my-1.5 rounded-lg border border-l-[3px] border-border border-l-orchestra bg-bg-elev"
    >
      <CollapsibleTrigger className="subagent-head flex w-full items-center gap-1.5 px-2.5 py-1.5 text-left text-sm">
        <span className="subagent-dot inline-flex">{STATUS_ICON[entry.status]}</span>
        <b>{entry.agentName}</b>
        <span className="truncate text-xs text-muted"> — {entry.task}</span>
        {entry.costUsd != null && entry.costUsd > 0 && (
          <span className="text-xs text-muted"> · ${entry.costUsd.toFixed(4)}</span>
        )}
        <span className="subagent-toggle ml-auto text-muted">
          {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
        </span>
      </CollapsibleTrigger>
      <CollapsibleContent className="subagent-body flex flex-col gap-0.5 px-2.5 pb-2">
        {entry.lines.length === 0 && <div className="text-xs text-muted">no activity yet</div>}
        {entry.lines.map((l, i) => (
          <div key={i} className="subagent-line font-mono text-xs text-muted">
            {l}
          </div>
        ))}
      </CollapsibleContent>
    </Collapsible>
  );
}
