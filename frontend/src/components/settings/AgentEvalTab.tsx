// Agent / trajectory evaluation (Phase 16, PLAN §16).
// Run a case set against an agent + optional pattern override, then compare
// runs on quality ↔ cost ↔ latency.

import { AlertTriangle, CheckCircle2, XCircle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  api,
  type Agent,
  type AgentEvalCase,
  type AgentEvalCaseInput,
  type AgentEvalRun,
  type PatternInfo,
} from "../../api/client";
import { selectCls } from "./formStyles";
import { Button } from "@/components/ui/button";
import { ErrorBanner } from "@/components/ui/error-banner";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

const BLANK_CASE: AgentEvalCaseInput = {
  prompt: "",
  expected_outcome: "",
  optimal_steps: null,
  forbidden_tools: [],
  held_out: false,
};

function pct(v: number | null): string {
  return v == null ? "—" : `${Math.round(v * 100)}%`;
}
function num(v: number | null, d = 2): string {
  return v == null ? "—" : v.toFixed(d);
}

export default function AgentEvalTab() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [patterns, setPatterns] = useState<PatternInfo[]>([]);
  const [agentId, setAgentId] = useState("");
  const [pattern, setPattern] = useState<string>("");
  const [name, setName] = useState("");
  const [casesText, setCasesText] = useState(
    JSON.stringify([BLANK_CASE], null, 2),
  );
  const [runs, setRuns] = useState<AgentEvalRun[]>([]);
  const [detail, setDetail] = useState<{ run: AgentEvalRun; cases: AgentEvalCase[] } | null>(
    null,
  );
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.listAgents().then((a) => {
      setAgents(a);
      if (a[0]) setAgentId(a[0].id);
    });
    api.listPatterns().then(setPatterns).catch(() => setPatterns([]));
  }, []);

  const reload = () => {
    if (agentId) api.agentEvalRuns(agentId).then(setRuns).catch(() => setRuns([]));
  };
  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId]);

  // poll while any run is still executing
  useEffect(() => {
    if (!runs.some((r) => r.status === "running")) return;
    const t = setInterval(reload, 2000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runs]);

  const compareRows = useMemo(
    () => [...runs].sort((a, b) => a.created_at.localeCompare(b.created_at)),
    [runs],
  );

  const [synthing, setSynthing] = useState(false);
  async function synth() {
    if (!agentId) return;
    setSynthing(true);
    setErr(null);
    try {
      const { cases } = await api.synthAgentEvalCases(agentId, 8);
      if (cases.length) setCasesText(JSON.stringify(cases, null, 2));
      else setErr("synthesis returned nothing (no judge model configured?)");
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setSynthing(false);
    }
  }

  async function start() {
    setErr(null);
    let cases: AgentEvalCaseInput[];
    try {
      cases = JSON.parse(casesText);
      if (!Array.isArray(cases) || cases.length === 0) throw new Error();
    } catch {
      setErr("cases must be a non-empty JSON array");
      return;
    }
    try {
      await api.createAgentEvalRun({
        name: name || pattern || "run",
        agent_id: agentId,
        pattern: pattern || null,
        cases,
      });
      setName("");
      reload();
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  return (
    <div className="settings-section flex flex-col gap-3">
      <div className="settings-section-head mb-3 flex items-center justify-between">
        <h2 className="text-lg font-semibold">Agent evaluation</h2>
      </div>
      <ErrorBanner message={err} />

      <div className="settings-form flex flex-col gap-3 rounded-lg border border-border bg-bg-elev p-4">
        <div className="form-row flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-2xs text-muted">
            Agent
            <select className={selectCls} value={agentId} onChange={(e) => setAgentId(e.target.value)}>
              {agents.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-2xs text-muted">
            Pattern override
            <select className={selectCls} value={pattern} onChange={(e) => setPattern(e.target.value)}>
              <option value="">(agent's own)</option>
              {patterns.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-1 flex-col gap-1 text-2xs text-muted">
            Run name
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </label>
        </div>
        <label className="flex flex-col gap-1 text-2xs text-muted">
          Cases (JSON — {"{ prompt, expected_outcome?, optimal_steps?, forbidden_tools?, held_out? }"}
          ). Mark a case <code>held_out: true</code> to keep it out of the Phase 17 Weakness
          Miner's input pool — needed for a fair regression check in the agent's Harness
          History.
          <Textarea
            rows={8}
            className="font-mono text-xs"
            value={casesText}
            onChange={(e) => setCasesText(e.target.value)}
          />
        </label>
        <div className="form-actions flex gap-2">
          <Button variant="outline" onClick={synth} disabled={!agentId || synthing}>
            {synthing ? "generating…" : "Suggest cases from agent config"}
          </Button>
          <Button onClick={start} disabled={!agentId}>
            Run eval
          </Button>
        </div>
      </div>

      <h3 className="mb-0">Runs — quality ↔ cost ↔ latency</h3>
      <div className="overflow-x-auto">
        <table className="settings-table w-full border-collapse text-sm [&_td]:border-b [&_td]:border-border [&_td]:px-2.5 [&_td]:py-2 [&_td]:align-middle [&_th]:border-b [&_th]:border-border [&_th]:px-2.5 [&_th]:py-2 [&_th]:text-left [&_th]:font-semibold [&_th]:text-muted">
          <thead>
            <tr>
              <th>Run</th>
              <th>Pattern</th>
              <th>Resolved</th>
              <th>Step eff.</th>
              <th>Redundant</th>
              <th>Param halluc.</th>
              <th>Forbidden</th>
              <th>LLM calls</th>
              <th>$ / case</th>
              <th>ms / case</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {compareRows.map((r) => (
              <tr key={r.id}>
                <td>{r.name || r.id.slice(0, 6)}</td>
                <td>{r.pattern ?? "—"}</td>
                <td>{r.status === "done" ? pct(r.task_resolution_rate) : r.status}</td>
                <td>{num(r.step_efficiency)}</td>
                <td>{num(r.redundant_tool_rate, 1)}</td>
                <td>{pct(r.parameter_hallucination_rate)}</td>
                <td>{pct(r.forbidden_tool_rate)}</td>
                <td>{num(r.avg_llm_calls, 1)}</td>
                <td>{r.avg_cost_usd == null ? "—" : `$${r.avg_cost_usd.toFixed(4)}`}</td>
                <td>{r.avg_latency_ms == null ? "—" : Math.round(r.avg_latency_ms)}</td>
                <td className="flex gap-2">
                  <Button
                    variant="link"
                    size="sm"
                    className="linkish h-auto px-1 py-0 text-2xs underline"
                    onClick={() => api.agentEvalRun(r.id).then(setDetail)}
                  >
                    cases
                  </Button>
                  <Button
                    variant="link"
                    size="sm"
                    className="linkish h-auto px-1 py-0 text-2xs underline"
                    onClick={() => api.deleteAgentEvalRun(r.id).then(reload)}
                  >
                    delete
                  </Button>
                </td>
              </tr>
            ))}
            {compareRows.length === 0 && (
              <tr>
                <td colSpan={11} className="text-muted">
                  No runs yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {detail && (
        <div className="settings-form flex flex-col gap-3 rounded-lg border border-border bg-bg-elev p-4">
          <h3 className="mt-0 flex items-center gap-2">
            {detail.run.name || detail.run.id.slice(0, 6)} — cases{" "}
            <Button
              variant="link"
              size="sm"
              className="linkish h-auto px-1 py-0 text-2xs underline"
              onClick={() => setDetail(null)}
            >
              close
            </Button>
          </h3>
          {detail.cases.map((c) => (
            <div key={c.id} className="rag-chunk rounded-lg border border-border p-2.5 text-sm">
              <div className="mb-1 flex flex-wrap items-center gap-1 text-xs text-muted">
                {c.resolved ? (
                  <CheckCircle2 className="size-3.5 text-success" />
                ) : (
                  <XCircle className="size-3.5 text-danger" />
                )}
                {c.resolved ? "resolved" : "unresolved"} · {c.steps_taken} steps ·{" "}
                {c.redundant_tool_calls} redundant · {c.llm_calls} LLM calls ·{" "}
                {c.cost_usd == null ? "—" : `$${c.cost_usd.toFixed(4)}`}
                {c.held_out && " · held-out"}
                {c.forbidden_tool_used && (
                  <span className="flex items-center gap-1">
                    <AlertTriangle className="size-3.5" /> used a forbidden tool
                  </span>
                )}
                {c.parameter_hallucination && (
                  <span className="flex items-center gap-1">
                    <AlertTriangle className="size-3.5" /> parameter hallucination
                  </span>
                )}
              </div>
              <b>{c.prompt}</b>
              <div className="text-2xs">{c.answer}</div>
              {(c.judge_rationale as { reason?: string })?.reason && (
                <div className="text-xs text-muted">
                  judge: {(c.judge_rationale as { reason?: string }).reason}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
