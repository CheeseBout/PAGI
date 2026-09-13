// Orchestration section of the agent editor (Phase 15b, SPEC §16 / §17).
// Pattern picker + the params for the chosen pattern + a rough cost estimate.

import { useEffect, useMemo, useState } from "react";
import { api, DelegatableAgent, PatternInfo } from "../../api/client";
import { ConfigForm } from "./ConfigForm";
import { selectCls } from "./formStyles";

type Props = {
  value: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
};

export function OrchestrationTab({ value, onChange }: Props) {
  const [patterns, setPatterns] = useState<PatternInfo[]>([]);
  const [agents, setAgents] = useState<DelegatableAgent[]>([]);

  useEffect(() => {
    api.listPatterns().then(setPatterns).catch(() => setPatterns([]));
    api.listDelegatableAgents().then(setAgents).catch(() => setAgents([]));
  }, []);

  const pattern = (value.pattern as string) || "react";
  const info = useMemo(
    () => patterns.find((p) => p.id === pattern),
    [patterns, pattern],
  );

  const onlyGroups = useMemo(
    () => ["shared", pattern],
    [pattern],
  );

  // rough live estimate of LLM calls per turn from the current config (SPEC §16.5)
  const estCalls = useMemo(() => {
    const n = (k: string, d: number) => Number(value[k] ?? d);
    switch (pattern) {
      case "react":
        return "1 + tool rounds";
      case "reflexion":
        return `~${3 * n("max_attempts", 3)}`;
      case "plan_execute":
        return `~${1 + 2 * n("max_steps", 6)}`;
      case "router":
        return "2 + branch";
      case "evaluator_optimizer":
        return `~${3 * n("max_rounds", 3)}`;
      case "supervisor": {
        const w = (value.worker_agent_ids as string[] | undefined)?.length ?? 0;
        return `~${2 + Math.min(w, n("max_workers", 3))} + each worker's own`;
      }
      case "debate": {
        const d = (value.debater_agent_ids as string[] | undefined)?.length ?? 2;
        return `~${d * (1 + n("rounds", 1)) + 1}`;
      }
      default:
        return info?.calls_hint ?? "?";
    }
  }, [pattern, value, info]);

  return (
    <div className="settings-form flex flex-col gap-3">
      <label className="flex flex-col gap-1 text-2xs text-muted">
        Design pattern
        <select
          className={selectCls}
          value={pattern}
          onChange={(e) => onChange({ ...value, pattern: e.target.value })}
        >
          {patterns.map((p) => (
            <option key={p.id} value={p.id}>
              {p.label} ({p.cost_tier})
            </option>
          ))}
        </select>
      </label>

      {info && (
        <p className="text-xs text-muted">
          {info.summary}
          <br />
          ≈ {estCalls} LLM calls / turn ({info.cost_tier})
          {info.requires_delegation && agents.length === 0 && (
            <>
              {" · "}
              <b>needs at least one delegatable agent</b>
            </>
          )}
        </p>
      )}

      {pattern !== "react" && (
        <ConfigForm
          which="orchestration"
          value={value}
          onChange={onChange}
          onlyGroups={onlyGroups}
          agents={agents.map((a) => ({ id: a.id, name: a.name }))}
        />
      )}
    </div>
  );
}
