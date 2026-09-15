// Multi-day project development loop (Phase 18, PLAN §18).
// Planner -> Developer -> QA, one git commit per iteration, driven by cron or
// a manual "run iteration now" — this tab creates/monitors/pauses runs.

import { useEffect, useState } from "react";
import {
  api,
  type DelegatableAgent,
  type ProjectIteration,
  type ProjectRun,
} from "../../api/client";
import { selectCls } from "./formStyles";
import { DataTable, SectionHead, SettingsForm, useErr } from "./shared";
import { confirmAction } from "@/store/confirmStore";
import { Button } from "@/components/ui/button";
import { ErrorBanner } from "@/components/ui/error-banner";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const EMPTY_FORM = {
  name: "",
  planner_agent_id: "",
  developer_agent_id: "",
  qa_agent_id: "",
  max_iterations: 10,
  budget_usd: 0,
  schedule: "",
};

function statusBadge(status: string): string {
  return {
    active: "text-success", paused: "text-warn", done: "text-muted",
    pass: "text-success", fail: "text-danger", qa_failed: "text-danger",
    running: "text-muted", error: "text-danger",
  }[status] ?? "text-muted";
}

function IterationsList({ projectId }: { projectId: string }) {
  const [its, setIts] = useState<ProjectIteration[] | null>(null);

  useEffect(() => {
    api.getProject(projectId).then((d) => setIts(d.iterations));
    const t = setInterval(() => api.getProject(projectId).then((d) => setIts(d.iterations)), 3000);
    return () => clearInterval(t);
  }, [projectId]);

  if (its === null) return <div className="text-xs text-muted">loading iterations…</div>;
  if (its.length === 0) return <div className="text-xs text-muted">No iterations yet.</div>;

  return (
    <div className="flex flex-col gap-1 text-xs">
      {[...its].reverse().map((it) => (
        <div
          key={it.id}
          className="flex flex-wrap items-center gap-2 rounded-md border border-border px-2 py-1"
        >
          <b>#{it.iteration_no}</b>
          <span className={statusBadge(it.status)}>{it.status}</span>
          {it.qa_verdict && <span className={statusBadge(it.qa_verdict)}>QA: {it.qa_verdict}</span>}
          {it.qa_reason && <span className="text-muted">({it.qa_reason})</span>}
          {it.workspace_commit_sha && <code className="text-2xs">{it.workspace_commit_sha.slice(0, 10)}</code>}
          {it.cost_usd > 0 && <span className="text-muted">${it.cost_usd.toFixed(4)}</span>}
          {it.error && <span className="text-danger">{it.error}</span>}
        </div>
      ))}
    </div>
  );
}

export default function ProjectsTab() {
  const { err, wrap } = useErr();
  const [projects, setProjects] = useState<ProjectRun[]>([]);
  const [agents, setAgents] = useState<DelegatableAgent[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [expanded, setExpanded] = useState<string | null>(null);

  const reload = () => api.listProjects().then(setProjects);
  useEffect(() => {
    Promise.all([reload(), api.listDelegatableAgents().then(setAgents)]).finally(() =>
      setLoading(false),
    );
  }, []);

  // poll while any run is active (iteration counts / status can change from cron)
  useEffect(() => {
    if (!projects.some((p) => p.status === "active")) return;
    const t = setInterval(reload, 4000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projects]);

  const create = () =>
    wrap(async () => {
      await api.createProject({
        name: form.name,
        planner_agent_id: form.planner_agent_id,
        developer_agent_id: form.developer_agent_id,
        qa_agent_id: form.qa_agent_id,
        max_iterations: form.max_iterations,
        budget_usd: form.budget_usd,
        schedule: form.schedule.trim() || null,
      });
      setCreating(false);
      setForm(EMPTY_FORM);
      await reload();
    });

  return (
    <div className="settings-section">
      <SectionHead
        title="Projects"
        action={<Button onClick={() => setCreating(true)}>+ New project</Button>}
      />
      <ErrorBanner message={err} />
      <p className="mb-3 text-xs text-muted">
        A project run repeats Planner → Developer → QA, one git commit per iteration, until it
        hits its iteration cap, its budget, or three QA failures in a row (auto-paused).
      </p>

      <DataTable
        rows={projects}
        rowKey={(p) => p.id}
        loading={loading}
        emptyMessage="No projects yet."
        columns={[
          { header: "Name", render: (p) => p.name },
          { header: "Status", className: "font-medium", render: (p) => (
            <span className={statusBadge(p.status)}>{p.status}</span>
          ) },
          { header: "Iterations", render: (p) => `${p.iterations_done} / ${p.max_iterations}` },
          { header: "Spend", render: (p) => (
            p.budget_usd > 0 ? `$${p.spent_usd.toFixed(4)} / $${p.budget_usd.toFixed(2)}` : `$${p.spent_usd.toFixed(4)}`
          ) },
          { header: "Schedule", render: (p) => p.schedule ? <code>{p.schedule}</code> : "manual" },
          {
            header: "",
            className: "row-actions flex flex-wrap justify-end gap-1.5",
            render: (p) => (
              <>
                <Button
                  variant="outline" size="sm"
                  onClick={() => setExpanded(expanded === p.id ? null : p.id)}
                >
                  {expanded === p.id ? "Hide" : "Iterations"}
                </Button>
                <Button
                  variant="outline" size="sm" disabled={p.status !== "active"}
                  onClick={() => wrap(async () => { await api.iterateProjectNow(p.id); })}
                >
                  Run iteration
                </Button>
                {p.status === "active" && (
                  <Button variant="outline" size="sm" onClick={() => wrap(async () => { await api.pauseProject(p.id); await reload(); })}>
                    Pause
                  </Button>
                )}
                {p.status === "paused" && (
                  <Button variant="outline" size="sm" onClick={() => wrap(async () => { await api.resumeProject(p.id); await reload(); })}>
                    Resume
                  </Button>
                )}
                <Button
                  variant="destructive" size="sm"
                  onClick={() =>
                    wrap(async () => {
                      const ok = await confirmAction({ title: "Delete this project?", confirmLabel: "Delete", variant: "destructive" });
                      if (!ok) return;
                      await api.deleteProject(p.id);
                      await reload();
                    })
                  }
                >
                  Delete
                </Button>
              </>
            ),
          },
        ]}
      />

      {expanded && (
        <div className="mt-3 rounded-lg border border-border bg-bg-elev p-3">
          <IterationsList projectId={expanded} />
        </div>
      )}

      {creating && (
        <SettingsForm>
          <h3 className="m-0">New project</h3>
          <Label>
            Name
            <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </Label>
          <div className="form-row flex flex-wrap items-end gap-3">
            {(["planner_agent_id", "developer_agent_id", "qa_agent_id"] as const).map((field) => (
              <Label key={field}>
                {field.replace("_agent_id", "")}
                <select
                  className={selectCls}
                  value={form[field]}
                  onChange={(e) => setForm({ ...form, [field]: e.target.value })}
                >
                  <option value="">— pick an agent —</option>
                  {agents.map((a) => (
                    <option key={a.id} value={a.id}>{a.name}</option>
                  ))}
                </select>
              </Label>
            ))}
          </div>
          {agents.length === 0 && (
            <div className="text-xs text-muted">
              No delegatable agents yet — enable "Other agents may delegate to this one" on at
              least three agents in the Agents tab first.
            </div>
          )}
          <div className="form-row flex flex-wrap items-end gap-3">
            <Label>
              Max iterations
              <Input
                type="number" min={1}
                value={form.max_iterations}
                onChange={(e) => setForm({ ...form, max_iterations: Number(e.target.value) })}
              />
            </Label>
            <Label>
              Budget (USD, 0 = off)
              <Input
                type="number" min={0} step="0.01"
                value={form.budget_usd}
                onChange={(e) => setForm({ ...form, budget_usd: Number(e.target.value) })}
              />
            </Label>
            <Label>
              Cron schedule (blank = manual only)
              <Input
                value={form.schedule}
                onChange={(e) => setForm({ ...form, schedule: e.target.value })}
                placeholder="0 2 * * *"
              />
            </Label>
          </div>
          <div className="form-actions flex gap-2">
            <Button onClick={create}>Create</Button>
            <Button variant="outline" onClick={() => setCreating(false)}>Cancel</Button>
          </div>
        </SettingsForm>
      )}
    </div>
  );
}
