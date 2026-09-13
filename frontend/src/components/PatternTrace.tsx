// Persistent pattern-node timeline for a conversation (Phase 14e, SPEC §16.4).
// Reads /conversations/{id}/runs — survives closing the tab, unlike the live strip.

import {
  ArrowRight,
  CheckCheck,
  ChevronDown,
  ChevronRight,
  Cog,
  Dot,
  Gavel,
  GitBranch,
  Map,
  PenLine,
  RotateCcw,
  Scale,
  Sigma,
  XCircle,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { api, type AgentRunRow } from "../api/client";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { ErrorBanner } from "@/components/ui/error-banner";
import { cn } from "@/lib/utils";

const NODE_ICON: Record<string, ReactNode> = {
  plan: <Map className="size-3.5" />,
  execute: <Cog className="size-3.5" />,
  verify: <CheckCheck className="size-3.5" />,
  generate: <PenLine className="size-3.5" />,
  evaluate: <Scale className="size-3.5" />,
  reflect: <RotateCcw className="size-3.5" />,
  route: <GitBranch className="size-3.5" />,
  delegate: <ArrowRight className="size-3.5" />,
  synthesize: <Sigma className="size-3.5" />,
  critique: <XCircle className="size-3.5" />,
  judge: <Gavel className="size-3.5" />,
};

export function PatternTrace({ conversationId }: { conversationId: string }) {
  const [rows, setRows] = useState<AgentRunRow[] | null>(null);
  const [open, setOpen] = useState<Set<string>>(new Set());

  useEffect(() => {
    api
      .conversationRuns(conversationId)
      .then(setRows)
      .catch(() => setRows([]));
  }, [conversationId]);

  if (rows === null) return <div className="text-xs text-muted">loading trace…</div>;
  if (rows.length === 0)
    return (
      <div className="text-xs text-muted">No pattern activity — this conversation ran plain ReAct.</div>
    );

  return (
    <div className="pattern-trace overflow-hidden rounded-lg border border-border">
      {rows.map((r) => {
        const isOpen = open.has(r.id);
        return (
          <Collapsible
            key={r.id}
            open={isOpen}
            onOpenChange={(v) =>
              setOpen((s) => {
                const n = new Set(s);
                v ? n.add(r.id) : n.delete(r.id);
                return n;
              })
            }
            className={cn(
              "ptrace-row border-t border-border first:border-t-0",
              r.status === "failed" && "status-failed bg-danger/8",
            )}
          >
            <CollapsibleTrigger className="ptrace-head flex w-full items-baseline gap-2 px-2.5 py-1.5 text-left text-sm">
              {isOpen ? <ChevronDown className="size-3 text-muted" /> : <ChevronRight className="size-3 text-muted" />}
              <span className="ptrace-step inline-block min-w-[1.4em] text-right text-muted">{r.step_no}</span>
              <span className="ptrace-node flex items-center gap-1 font-semibold">
                <span className={r.status === "failed" ? "text-danger" : "text-orchestra"}>
                  {NODE_ICON[r.node] ?? <Dot className="size-3.5" />}
                </span>
                {r.node}
                {r.attempt > 1 && <sup> #{r.attempt}</sup>}
              </span>
              <span className="text-xs text-muted">
                {r.pattern} · {r.latency_ms}ms
                {r.cost_usd != null && r.cost_usd > 0 && ` · $${r.cost_usd.toFixed(4)}`}
                {r.status !== "done" && ` · ${r.status}`}
              </span>
            </CollapsibleTrigger>
            <CollapsibleContent className="ptrace-body flex flex-col gap-1 px-2.5 py-2 pl-8 text-xs text-muted">
              {r.input_summary && (
                <div>
                  <b>in:</b> {r.input_summary}
                </div>
              )}
              {r.output_summary && (
                <div>
                  <b>out:</b> {r.output_summary}
                </div>
              )}
              <ErrorBanner message={r.error} />
              {Object.keys(r.payload || {}).length > 0 && (
                <pre className="max-h-52 overflow-auto rounded-md bg-bg-alt p-1.5 font-mono">
                  {JSON.stringify(r.payload, null, 2)}
                </pre>
              )}
            </CollapsibleContent>
          </Collapsible>
        );
      })}
    </div>
  );
}
