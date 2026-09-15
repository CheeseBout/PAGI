// Phase 17 — self-improving harness: timeline of an agent's
// agent_config_versions (PLAN §17c). Same Collapsible-timeline shape as
// PatternTrace.tsx, but rows are harness patches instead of pattern steps,
// and each row (other than the currently active one) can be rolled back to.

import { CheckCircle2, ChevronDown, ChevronRight, Clock, History, XCircle } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { api, type AgentConfigVersion } from "../api/client";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Button } from "@/components/ui/button";
import { ErrorBanner } from "@/components/ui/error-banner";
import { confirmAction } from "@/store/confirmStore";
import { cn } from "@/lib/utils";

const STATUS_ICON: Record<string, ReactNode> = {
  active: <CheckCircle2 className="size-3.5 text-success" />,
  proposed: <Clock className="size-3.5 text-muted" />,
  rejected: <XCircle className="size-3.5 text-danger" />,
  superseded: <History className="size-3.5 text-muted" />,
};

function pct(v: number | null): string {
  return v == null ? "—" : `${Math.round(v * 100)}%`;
}

function fmtValue(v: unknown): string {
  if (typeof v === "string") return v;
  return JSON.stringify(v, null, 2);
}

export function HarnessHistory({ agentId }: { agentId: string }) {
  const [versions, setVersions] = useState<AgentConfigVersion[] | null>(null);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [err, setErr] = useState<string | null>(null);
  const [rollingBack, setRollingBack] = useState<string | null>(null);

  const reload = () =>
    api
      .listAgentConfigVersions(agentId)
      .then(setVersions)
      .catch((e) => setErr((e as Error).message));

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId]);

  // poll while a proposal is still awaiting the regression gate
  useEffect(() => {
    if (!versions?.some((v) => v.status === "proposed")) return;
    const t = setInterval(reload, 2000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [versions]);

  async function doRollback(versionId: string) {
    const ok = await confirmAction({
      title: "Roll back the agent to this config version?",
      confirmLabel: "Roll back",
    });
    if (!ok) return;
    setErr(null);
    setRollingBack(versionId);
    try {
      await api.rollbackAgentConfigVersion(agentId, versionId);
      await reload();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setRollingBack(null);
    }
  }

  if (versions === null) return <div className="text-xs text-muted">loading harness history…</div>;

  return (
    <div className="flex flex-col gap-2">
      <ErrorBanner message={err} />
      {versions.length === 0 ? (
        <div className="text-xs text-muted">
          No harness activity yet — patches appear here after the Phase 17 pipeline mines a
          recurring failure from this agent's eval runs (Agent evaluation tab) and proposes a fix.
        </div>
      ) : (
        <div className="pattern-trace overflow-hidden rounded-lg border border-border">
          {versions.map((v) => {
            const isOpen = open.has(v.id);
            const field = Object.keys(v.diff)[0];
            const parent = versions.find((p) => p.id === v.parent_version_id);
            const oldValue = parent ? (parent.config_snapshot as Record<string, unknown>)[field] : undefined;
            return (
              <Collapsible
                key={v.id}
                open={isOpen}
                onOpenChange={(o) =>
                  setOpen((s) => {
                    const n = new Set(s);
                    o ? n.add(v.id) : n.delete(v.id);
                    return n;
                  })
                }
                className={cn(
                  "ptrace-row border-t border-border first:border-t-0",
                  v.status === "rejected" && "status-failed bg-danger/8",
                )}
              >
                <CollapsibleTrigger className="ptrace-head flex w-full flex-wrap items-baseline gap-2 px-2.5 py-1.5 text-left text-sm">
                  {isOpen ? <ChevronDown className="size-3 text-muted" /> : <ChevronRight className="size-3 text-muted" />}
                  <span className="flex items-center gap-1 font-semibold">
                    {STATUS_ICON[v.status]}
                    {v.status}
                  </span>
                  <code className="text-xs">{field}</code>
                  <span className="text-xs text-muted">
                    {new Date(v.created_at).toLocaleString()}
                    {v.held_in_score != null && ` · held-in ${pct(v.held_in_score)}`}
                    {v.held_out_score != null && ` · held-out ${pct(v.held_out_score)}`}
                  </span>
                </CollapsibleTrigger>
                <CollapsibleContent className="ptrace-body flex flex-col gap-1.5 px-2.5 py-2 pl-8 text-xs text-muted">
                  {v.rationale && (
                    <div>
                      <b>rationale:</b> {v.rationale}
                    </div>
                  )}
                  {v.status === "rejected" && v.reject_reason && (
                    <div>
                      <b>rejected because:</b> {v.reject_reason}
                    </div>
                  )}
                  <div className="flex flex-col gap-1">
                    {oldValue !== undefined && (
                      <div>
                        <b>before:</b>
                        <pre className="max-h-40 overflow-auto rounded-md bg-bg-alt p-1.5 font-mono">
                          {fmtValue(oldValue)}
                        </pre>
                      </div>
                    )}
                    <div>
                      <b>after:</b>
                      <pre className="max-h-40 overflow-auto rounded-md bg-bg-alt p-1.5 font-mono">
                        {fmtValue(v.diff[field])}
                      </pre>
                    </div>
                  </div>
                  {v.status !== "active" && (
                    <Button
                      variant="outline"
                      size="sm"
                      className="mt-1 w-fit"
                      disabled={rollingBack === v.id}
                      onClick={() => doRollback(v.id)}
                    >
                      {rollingBack === v.id ? "rolling back…" : "Roll back to this version"}
                    </Button>
                  )}
                </CollapsibleContent>
              </Collapsible>
            );
          })}
        </div>
      )}
    </div>
  );
}
