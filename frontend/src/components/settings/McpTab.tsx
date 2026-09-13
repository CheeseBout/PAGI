import { useEffect, useState } from "react";
import { api, type McpServer } from "../../api/client";
import { checkboxCls, selectCls } from "./formStyles";
import { DataTable, SectionHead, SettingsForm, useErr } from "./shared";
import { confirmAction } from "@/store/confirmStore";
import { Button } from "@/components/ui/button";
import { ErrorBanner } from "@/components/ui/error-banner";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

export default function McpTab() {
  const { err, wrap } = useErr();
  const [servers, setServers] = useState<McpServer[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<string | "new" | null>(null);
  const [form, setForm] = useState<{ name: string; transport: string; config: string; enabled: boolean }>({
    name: "",
    transport: "stdio",
    config: "{}",
    enabled: true,
  });

  const reload = () => api.listMcpServers().then(setServers);
  useEffect(() => {
    reload().finally(() => setLoading(false));
  }, []);

  const save = () =>
    wrap(async () => {
      let config: Record<string, unknown>;
      try {
        config = JSON.parse(form.config);
      } catch {
        throw new Error("config is not valid JSON");
      }
      const body = {
        name: form.name,
        transport: form.transport as McpServer["transport"],
        config,
        enabled: form.enabled,
      };
      if (editing === "new") await api.createMcpServer(body);
      else if (editing) await api.updateMcpServer(editing, body);
      setEditing(null);
      await reload();
    });

  return (
    <div className="settings-section">
      <SectionHead
        title="MCP servers"
        action={
          <Button
            onClick={() => {
              setEditing("new");
              setForm({ name: "", transport: "stdio", config: "{}", enabled: true });
            }}
          >
            + New server
          </Button>
        }
      />
      <ErrorBanner message={err} />
      <DataTable
        rows={servers}
        rowKey={(s) => s.id}
        loading={loading}
        emptyMessage="No MCP servers registered yet."
        columns={[
          { header: "Name", render: (s) => s.name },
          { header: "Transport", render: (s) => s.transport },
          { header: "Enabled", render: (s) => (s.enabled ? "✓" : "—") },
          {
            header: "",
            className: "row-actions flex justify-end gap-1.5",
            render: (s) => (
              <>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setEditing(s.id);
                    setForm({
                      name: s.name,
                      transport: s.transport,
                      config: JSON.stringify(s.config, null, 2),
                      enabled: s.enabled,
                    });
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
                        title: "Delete this server?",
                        confirmLabel: "Delete",
                        variant: "destructive",
                      });
                      if (ok) {
                        await api.deleteMcpServer(s.id);
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
          <h3 className="m-0">{editing === "new" ? "New MCP server" : "Edit MCP server"}</h3>
          <div className="form-row flex flex-wrap items-end gap-3">
            <Label className="grow flex-1">
              Name
              <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </Label>
            <Label>
              Transport
              <select
                className={selectCls}
                value={form.transport}
                onChange={(e) => setForm({ ...form, transport: e.target.value })}
              >
                <option value="stdio">stdio</option>
                <option value="sse">sse</option>
                <option value="http">http</option>
              </select>
            </Label>
            <Label className="checkbox">
              <input
                type="checkbox"
                className={checkboxCls}
                checked={form.enabled}
                onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
              />
              Enabled
            </Label>
          </div>
          <Label>
            Config (JSON)
            <Textarea
              className="font-mono"
              rows={6}
              value={form.config}
              onChange={(e) => setForm({ ...form, config: e.target.value })}
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
