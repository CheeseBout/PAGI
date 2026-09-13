import { useEffect, useState } from "react";
import { api, type Agent, type CronJob } from "../../api/client";
import { checkboxCls, selectCls } from "./formStyles";
import { DataTable, SectionHead, SettingsForm, useErr } from "./shared";
import { confirmAction } from "@/store/confirmStore";
import { Button } from "@/components/ui/button";
import { ErrorBanner } from "@/components/ui/error-banner";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

export default function CronTab() {
  const { err, wrap } = useErr();
  const [jobs, setJobs] = useState<CronJob[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<string | "new" | null>(null);
  const [form, setForm] = useState<Partial<CronJob>>({});
  const [runMsg, setRunMsg] = useState<string | null>(null);

  const reload = () => api.listCronJobs().then(setJobs);
  useEffect(() => {
    Promise.all([reload(), api.listAgents().then(setAgents)]).finally(() => setLoading(false));
  }, []);

  const save = () =>
    wrap(async () => {
      const body = {
        ...form,
        unattended_allowed_tools:
          typeof form.unattended_allowed_tools === "string"
            ? (form.unattended_allowed_tools as unknown as string)
                .split(",")
                .map((s) => s.trim())
                .filter(Boolean)
            : form.unattended_allowed_tools || [],
      };
      if (editing === "new") await api.createCronJob(body);
      else if (editing) await api.updateCronJob(editing, body);
      setEditing(null);
      await reload();
    });

  return (
    <div className="settings-section">
      <SectionHead
        title="Cron jobs"
        action={
          <Button
            onClick={() => {
              setEditing("new");
              setForm({ enabled: true, agent_id: agents[0]?.id, schedule: "0 9 * * *" });
            }}
          >
            + New job
          </Button>
        }
      />
      <ErrorBanner message={err} />
      {runMsg && (
        <div role="status" className="mb-3 rounded-lg bg-success/10 px-2.5 py-2 text-sm text-success">
          {runMsg}
        </div>
      )}
      <DataTable
        rows={jobs}
        rowKey={(j) => j.id}
        loading={loading}
        emptyMessage="No cron jobs yet."
        columns={[
          { header: "Name", render: (j) => j.name },
          { header: "Schedule", render: (j) => <code>{j.schedule}</code> },
          { header: "Agent", render: (j) => agents.find((a) => a.id === j.agent_id)?.name || "?" },
          { header: "Enabled", render: (j) => (j.enabled ? "✓" : "—") },
          {
            header: "Next run",
            className: "text-xs text-muted",
            render: (j) => j.next_run_at?.slice(0, 16).replace("T", " ") || "—",
          },
          {
            header: "",
            className: "row-actions flex justify-end gap-1.5",
            render: (j) => (
              <>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() =>
                    wrap(async () => {
                      await api.runCronJob(j.id);
                      setRunMsg("Job started — check the sidebar for its conversation.");
                      setTimeout(() => setRunMsg(null), 5000);
                    })
                  }
                >
                  Run now
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setEditing(j.id);
                    setForm(j);
                  }}
                >
                  Edit
                </Button>
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={() =>
                    wrap(async () => {
                      const ok = await confirmAction({
                        title: "Delete this job?",
                        confirmLabel: "Delete",
                        variant: "destructive",
                      });
                      if (ok) {
                        await api.deleteCronJob(j.id);
                        await reload();
                      }
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

      {editing && (
        <SettingsForm>
          <h3 className="m-0">{editing === "new" ? "New cron job" : "Edit cron job"}</h3>
          <Label>
            Name
            <Input value={form.name || ""} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </Label>
          <div className="form-row flex flex-wrap items-end gap-3">
            <Label>
              Schedule (5-field cron)
              <Input
                value={form.schedule || ""}
                onChange={(e) => setForm({ ...form, schedule: e.target.value })}
                placeholder="0 9 * * *"
              />
            </Label>
            <Label>
              Agent
              <select
                className={selectCls}
                value={form.agent_id || ""}
                onChange={(e) => setForm({ ...form, agent_id: e.target.value })}
              >
                {agents.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
            </Label>
            <Label className="checkbox">
              <input
                type="checkbox"
                className={checkboxCls}
                checked={form.enabled ?? true}
                onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
              />
              Enabled
            </Label>
          </div>
          <Label>
            Prompt
            <Textarea
              rows={3}
              value={form.prompt || ""}
              onChange={(e) => setForm({ ...form, prompt: e.target.value })}
            />
          </Label>
          <Label>
            Unattended-allowed tools (comma separated)
            <Input
              value={
                Array.isArray(form.unattended_allowed_tools)
                  ? form.unattended_allowed_tools.join(", ")
                  : (form.unattended_allowed_tools as unknown as string) || ""
              }
              onChange={(e) =>
                setForm({ ...form, unattended_allowed_tools: e.target.value as unknown as string[] })
              }
            />
          </Label>
          <div className="form-actions flex gap-2">
            <Button onClick={save}>Save</Button>
            <Button variant="outline" onClick={() => setEditing(null)}>
              Cancel
            </Button>
          </div>
        </SettingsForm>
      )}
    </div>
  );
}
