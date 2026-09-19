import { useEffect, useRef, useState } from "react";
import { api, type Agent, type AgentInput, type KbCollection, type OpenRouterModel } from "../../api/client";
import { HarnessHistory } from "../HarnessHistory";
import { ConfigForm } from "./ConfigForm";
import { checkboxCls, selectCls } from "./formStyles";
import { OrchestrationTab } from "./OrchestrationTab";
import { DataTable, SectionHead, SettingsForm, useErr } from "./shared";
import { confirmAction } from "@/store/confirmStore";
import { Button } from "@/components/ui/button";
import { ErrorBanner } from "@/components/ui/error-banner";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

const BUILTIN_TOOLS = [
  "fetch_url",
  "web_search",
  "get_current_datetime",
  "http_request",
  "execute_code",
  "read_file",
  "write_file",
  "edit_file",
  "delete_file",
  "list_files",
  "search_files",
  "delegate_task",
];
const PROVIDERS = ["openai", "anthropic", "gemini", "openrouter"];

const EMPTY_AGENT: AgentInput = {
  name: "",
  system_prompt: "",
  provider: "anthropic",
  model: "",
  tools_allowed: [],
  tool_policy: {},
  temperature: 0.7,
  max_tokens: 4096,
  is_default: false,
  vision_enabled: true,
  kb_collection_ids: [],
  rag_config: {},
  is_delegatable: false,
  delegate_description: "",
  orchestration: {},
  avatar_config: {},
};

/** Warn on tab-close/refresh while a form is open — the one dirty-guard that
 * doesn't need a data router (in-app tab switches aren't blocked; this app
 * uses plain <Routes>, and useBlocker/unstable_usePrompt need createBrowserRouter). */
function useWarnOnUnload(active: boolean) {
  useEffect(() => {
    if (!active) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [active]);
}

export default function AgentsTab() {
  const { err, wrap } = useErr();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [collections, setCollections] = useState<KbCollection[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<string | "new" | null>(null);
  const [form, setForm] = useState<AgentInput>(EMPTY_AGENT);
  const [price, setPrice] = useState<string>("");
  const [touched, setTouched] = useState(false);
  const [freeModels, setFreeModels] = useState<OpenRouterModel[]>([]);
  const [freeModelsErr, setFreeModelsErr] = useState<string>("");
  const [freeModelsLoading, setFreeModelsLoading] = useState(false);
  const [avatarModels, setAvatarModels] = useState<string[]>([]);
  const [avatarModelsErr, setAvatarModelsErr] = useState<string>("");
  const [avatarModelsLoading, setAvatarModelsLoading] = useState(false);
  const [avatarUploading, setAvatarUploading] = useState(false);
  const [avatarUploadErr, setAvatarUploadErr] = useState<string>("");
  const avatarUploadInputRef = useRef<HTMLInputElement | null>(null);

  useWarnOnUnload(editing !== null);

  // `webkitdirectory`/`directory` aren't in React's typed <input> props —
  // set imperatively so TS doesn't need an `any` cast in the JSX below.
  useEffect(() => {
    const el = avatarUploadInputRef.current;
    if (!el) return;
    el.setAttribute("webkitdirectory", "");
    el.setAttribute("directory", "");
  }, []);

  // Uploads always go to the shared library (SPEC §20.5) — one upload,
  // usable by every agent's picker, no per-agent setup, and it works even
  // for a not-yet-saved "new" agent (the shared library isn't agent-scoped).
  async function handleAvatarFolderPicked(e: React.ChangeEvent<HTMLInputElement>) {
    const files = e.target.files;
    e.target.value = ""; // let the same folder be re-picked later (re-upload)
    if (!files || files.length === 0 || !editing) return;
    setAvatarUploading(true);
    setAvatarUploadErr("");
    try {
      const result = await api.uploadSharedAvatarModel(files);
      setAvatarModels((prev) => Array.from(new Set([...prev, ...result.models])).sort());
      setAvatar({ model_path: result.model_path });
    } catch (err) {
      setAvatarUploadErr(err instanceof Error ? err.message : "upload failed");
    } finally {
      setAvatarUploading(false);
    }
  }

  useEffect(() => {
    if (!editing || form.provider !== "openrouter") return;
    let live = true;
    setFreeModelsLoading(true);
    setFreeModelsErr("");
    api
      .openrouterModels()
      .then((models) => live && setFreeModels(models))
      .catch((e) => live && setFreeModelsErr(e?.message || "failed to load OpenRouter models"))
      .finally(() => live && setFreeModelsLoading(false));
    return () => {
      live = false;
    };
  }, [editing, form.provider]);

  // Models are discovered on disk rather than typed by hand: a real agent's
  // picker merges its own directory with the shared library server-side
  // (api.listAvatarModels); a not-yet-saved "new" agent has no directory of
  // its own yet, so it sees the shared library directly instead.
  useEffect(() => {
    if (!editing) {
      setAvatarModels([]);
      return;
    }
    let live = true;
    setAvatarModelsLoading(true);
    setAvatarModelsErr("");
    const req = editing === "new" ? api.listSharedAvatarModels() : api.listAvatarModels(editing);
    req
      .then((r) => live && setAvatarModels(r.models))
      .catch((e) => live && setAvatarModelsErr(e?.message || "failed to list avatar models"))
      .finally(() => live && setAvatarModelsLoading(false));
    return () => {
      live = false;
    };
  }, [editing]);

  const reload = () => api.listAgents().then(setAgents);
  useEffect(() => {
    Promise.all([
      reload(),
      api.listKbCollections().then(setCollections).catch(() => setCollections([])),
    ]).finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!form.model) {
      setPrice("");
      return;
    }
    let live = true;
    api
      .modelPricing(form.model, form.provider)
      .then((p) => {
        if (!live) return;
        setPrice(
          p.known === false
            ? "price unknown"
            : `~$${p.input_per_1m}/1M in · $${p.output_per_1m}/1M out (${p.source})`,
        );
      })
      .catch(() => live && setPrice(""));
    return () => {
      live = false;
    };
  }, [form.model, form.provider]);

  function startEdit(a: Agent) {
    setTouched(false);
    setEditing(a.id);
    setForm({
      name: a.name,
      system_prompt: a.system_prompt || "",
      provider: a.provider,
      model: a.model,
      tools_allowed: a.tools_allowed || [],
      tool_policy: a.tool_policy || {},
      temperature: a.temperature ?? 0.7,
      max_tokens: a.max_tokens ?? 4096,
      is_default: a.is_default,
      vision_enabled: a.vision_enabled ?? true,
      kb_collection_ids: a.kb_collection_ids || [],
      rag_config: a.rag_config || {},
      is_delegatable: a.is_delegatable ?? false,
      delegate_description: a.delegate_description || "",
      orchestration: a.orchestration || {},
      avatar_config: a.avatar_config || {},
    });
  }

  function setAvatar(patch: Record<string, unknown>) {
    setForm((f) => ({ ...f, avatar_config: { ...(f.avatar_config || {}), ...patch } }));
  }

  function toggleCollection(id: string) {
    setForm((f) => {
      const cur = f.kb_collection_ids || [];
      const has = cur.includes(id);
      return { ...f, kb_collection_ids: has ? cur.filter((c) => c !== id) : [...cur, id] };
    });
  }

  function toggleTool(name: string) {
    setForm((f) => {
      const has = f.tools_allowed.includes(name);
      return { ...f, tools_allowed: has ? f.tools_allowed.filter((t) => t !== name) : [...f.tools_allowed, name] };
    });
  }
  function setPolicy(name: string, value: string) {
    setForm((f) => ({ ...f, tool_policy: { ...f.tool_policy, [name]: value } }));
  }

  const nameError = touched && !form.name.trim() ? "Name is required" : null;
  const modelError = touched && !form.model.trim() ? "Model is required" : null;

  const save = () => {
    setTouched(true);
    if (!form.name.trim() || !form.model.trim()) return;
    return wrap(async () => {
      if (editing === "new") await api.createAgent(form);
      else if (editing) await api.updateAgent(editing, form);
      setEditing(null);
      await reload();
    });
  };
  const remove = (id: string) =>
    wrap(async () => {
      const ok = await confirmAction({ title: "Delete this agent?", confirmLabel: "Delete", variant: "destructive" });
      if (!ok) return;
      await api.deleteAgent(id);
      await reload();
    });

  return (
    <div className="settings-section">
      <SectionHead
        title="Agents"
        action={
          <Button
            onClick={() => {
              setTouched(false);
              setEditing("new");
              setForm(EMPTY_AGENT);
            }}
          >
            + New agent
          </Button>
        }
      />
      <ErrorBanner message={err} />

      <DataTable
        rows={agents}
        rowKey={(a) => a.id}
        loading={loading}
        emptyMessage="No agents yet."
        columns={[
          { header: "Name", render: (a) => a.name },
          {
            header: "Provider / model",
            render: (a) => (
              <code>
                {a.provider}/{a.model}
              </code>
            ),
          },
          { header: "Tools", render: (a) => a.tools_allowed?.length ?? 0 },
          { header: "Default", render: (a) => (a.is_default ? "✓" : "") },
          {
            header: "",
            className: "row-actions flex justify-end gap-1.5",
            render: (a) => (
              <>
                <Button variant="outline" size="sm" onClick={() => startEdit(a)}>
                  Edit
                </Button>
                <Button variant="destructive" size="sm" onClick={() => remove(a.id)}>
                  Delete
                </Button>
              </>
            ),
          },
        ]}
      />

      {editing && (
        <SettingsForm key={editing}>
          <h3 className="m-0">{editing === "new" ? "New agent" : "Edit agent"}</h3>
          <Label>
            Name
            <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            {nameError && <span className="text-xs text-danger">{nameError}</span>}
          </Label>
          <div className="form-row flex flex-wrap items-end gap-3">
            <Label>
              Provider
              <select
                className={selectCls}
                value={form.provider}
                onChange={(e) => setForm({ ...form, provider: e.target.value })}
              >
                {PROVIDERS.map((p) => (
                  <option key={p}>{p}</option>
                ))}
              </select>
            </Label>
            <Label className="grow flex-1">
              Model
              <Input value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} />
              {modelError ? (
                <span className="text-xs text-danger">{modelError}</span>
              ) : (
                <span className="text-xs text-muted">{price}</span>
              )}
            </Label>
          </div>
          {form.provider === "openrouter" && (
            <Label>
              Free OpenRouter models{freeModels.length > 0 && ` (${freeModels.length})`}
              <select
                className={selectCls}
                value=""
                disabled={freeModelsLoading || freeModels.length === 0}
                onChange={(e) => {
                  if (e.target.value) setForm({ ...form, model: e.target.value });
                }}
              >
                <option value="">
                  {freeModelsLoading ? "Loading…" : "— pick a free model —"}
                </option>
                {freeModels.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name} · {m.id}
                    {m.context_length ? ` (${m.context_length.toLocaleString()} ctx)` : ""}
                  </option>
                ))}
              </select>
              {freeModelsErr && <span className="text-xs text-danger">{freeModelsErr}</span>}
            </Label>
          )}
          <Label>
            System prompt
            <Textarea
              rows={4}
              value={form.system_prompt}
              onChange={(e) => setForm({ ...form, system_prompt: e.target.value })}
            />
          </Label>
          <div className="form-row flex flex-wrap items-end gap-3">
            <Label>
              Temperature
              <Input
                type="number"
                step="0.1"
                value={form.temperature}
                onChange={(e) => setForm({ ...form, temperature: Number(e.target.value) })}
              />
            </Label>
            <Label>
              Max tokens
              <Input
                type="number"
                value={form.max_tokens}
                onChange={(e) => setForm({ ...form, max_tokens: Number(e.target.value) })}
              />
            </Label>
            <Label className="checkbox">
              <input
                type="checkbox"
                className={checkboxCls}
                checked={form.is_default}
                onChange={(e) => setForm({ ...form, is_default: e.target.checked })}
              />
              Default agent
            </Label>
            <Label
              className="checkbox"
              title="Whether this agent can see image attachments (chat uploads, future screenshots). Off = images are stripped before the LLM call."
            >
              <input
                type="checkbox"
                className={checkboxCls}
                checked={form.vision_enabled ?? true}
                onChange={(e) => setForm({ ...form, vision_enabled: e.target.checked })}
              />
              Vision
            </Label>
          </div>

          <details className="config-group rounded-lg border border-border px-2.5 py-1.5" open={form.tools_allowed.length > 0}>
            <summary className="cursor-pointer text-sm font-semibold text-muted">
              Tools {form.tools_allowed.length > 0 && `(${form.tools_allowed.length} allowed)`}
            </summary>
            <div className="mt-2">
              {BUILTIN_TOOLS.map((t) => {
                const on = form.tools_allowed.includes(t);
                return (
                  <div key={t} className="tool-row flex items-center gap-2.5 py-0.5">
                    <Label className="checkbox">
                      <input type="checkbox" className={checkboxCls} checked={on} onChange={() => toggleTool(t)} />
                      <code>{t}</code>
                    </Label>
                    {on && (
                      <select
                        className={cn(selectCls, "h-7 px-1.5 py-0.5 text-xs")}
                        value={form.tool_policy[t] || ""}
                        onChange={(e) => setPolicy(t, e.target.value)}
                      >
                        <option value="">(default)</option>
                        <option value="auto">auto</option>
                        <option value="ask">ask</option>
                      </select>
                    )}
                  </div>
                );
              })}
            </div>
          </details>

          <details
            className="config-group rounded-lg border border-border px-2.5 py-1.5"
            open={(form.kb_collection_ids || []).length > 0}
          >
            <summary className="cursor-pointer text-sm font-semibold text-muted">
              Knowledge base (RAG)
              {(form.kb_collection_ids || []).length > 0 && ` (${form.kb_collection_ids!.length} collection(s))`}
            </summary>
            <div className="mt-2">
              {collections.length === 0 && (
                <div className="text-xs text-muted">No collections yet — create one in the Knowledge tab.</div>
              )}
              {collections.map((c) => (
                <div key={c.id} className="tool-row flex items-center gap-2.5 py-0.5">
                  <Label className="checkbox">
                    <input
                      type="checkbox"
                      className={checkboxCls}
                      checked={(form.kb_collection_ids || []).includes(c.id)}
                      onChange={() => toggleCollection(c.id)}
                    />
                    {c.name}
                    <span className="text-xs text-muted">
                      {" "}
                      ({c.doc_count} docs / {c.chunk_count} chunks)
                    </span>
                  </Label>
                </div>
              ))}
              {(form.kb_collection_ids || []).length > 0 && (
                <ConfigForm
                  which="rag"
                  value={form.rag_config || {}}
                  onChange={(v) => setForm({ ...form, rag_config: v })}
                  parentLayers={(() => {
                    const first = collections.find((c) => c.id === (form.kb_collection_ids || [])[0]);
                    return first ? [{ label: `collection «${first.name}»`, raw: first.config || {} }] : [];
                  })()}
                />
              )}
            </div>
          </details>

          <details
            className="config-group rounded-lg border border-border px-2.5 py-1.5"
            open={form.is_delegatable ?? false}
          >
            <summary className="cursor-pointer text-sm font-semibold text-muted">
              Delegation{form.is_delegatable ? " (worker)" : ""}
            </summary>
            <div className="mt-2 flex flex-col gap-2">
              <Label className="checkbox">
                <input
                  type="checkbox"
                  className={checkboxCls}
                  checked={form.is_delegatable ?? false}
                  onChange={(e) => setForm({ ...form, is_delegatable: e.target.checked })}
                />
                Other agents may delegate to this one (worker)
              </Label>
              {form.is_delegatable && (
                <Label>
                  "Good at…" description (helps supervisors route to it)
                  <Input
                    value={form.delegate_description || ""}
                    onChange={(e) => setForm({ ...form, delegate_description: e.target.value })}
                    placeholder="e.g. writes and runs Python in the sandbox"
                  />
                </Label>
              )}
            </div>
          </details>

          <details
            className="config-group rounded-lg border border-border px-2.5 py-1.5"
            open={Boolean((form.avatar_config as Record<string, unknown> | undefined)?.enabled)}
          >
            <summary className="cursor-pointer text-sm font-semibold text-muted">
              Avatar (2D){(form.avatar_config as { enabled?: boolean } | undefined)?.enabled ? " (on)" : ""}
            </summary>
            <div className="mt-2 flex flex-col gap-2">
              <Label className="checkbox">
                <input
                  type="checkbox"
                  className={checkboxCls}
                  checked={Boolean((form.avatar_config as { enabled?: boolean } | undefined)?.enabled)}
                  onChange={(e) => setAvatar({ enabled: e.target.checked })}
                />
                Show a Live2D avatar beside the chat for this agent
              </Label>
              {Boolean((form.avatar_config as { enabled?: boolean } | undefined)?.enabled) && (
                <>
                  <Label>
                    Model
                    {(() => {
                      const modelPath =
                        (form.avatar_config as { model_path?: string } | undefined)?.model_path || "";
                      const options =
                        modelPath && !avatarModels.includes(modelPath)
                          ? [modelPath, ...avatarModels]
                          : avatarModels;
                      return (
                        <>
                          <div className="flex items-center gap-2">
                            <select
                              className={cn(selectCls, "flex-1")}
                              value={modelPath}
                              disabled={avatarModelsLoading}
                              onChange={(e) => setAvatar({ model_path: e.target.value })}
                            >
                              <option value="">
                                {avatarModelsLoading
                                  ? "Loading…"
                                  : options.length === 0
                                    ? "— no models found —"
                                    : "— pick a model —"}
                              </option>
                              {options.map((m) => (
                                <option key={m} value={m}>
                                  {m}
                                </option>
                              ))}
                            </select>
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              disabled={avatarUploading}
                              onClick={() => avatarUploadInputRef.current?.click()}
                            >
                              {avatarUploading ? "Uploading…" : "Upload folder"}
                            </Button>
                            <input
                              ref={avatarUploadInputRef}
                              type="file"
                              multiple
                              className="hidden"
                              onChange={handleAvatarFolderPicked}
                            />
                          </div>
                          {avatarModelsErr && (
                            <span className="text-xs text-danger">{avatarModelsErr}</span>
                          )}
                          {avatarUploadErr && (
                            <span className="text-xs text-danger">{avatarUploadErr}</span>
                          )}
                          {!avatarModelsErr && options.length === 0 && !avatarModelsLoading && (
                            <span className="text-xs text-muted">
                              No models in the shared library yet{editing !== "new" ? " (and none placed for this agent specifically)" : ""}
                              — click "Upload folder" and pick a model's folder (containing its{" "}
                              <code>*.model3.json</code>). Uploaded here, it's available to every agent.
                            </span>
                          )}
                        </>
                      );
                    })()}
                  </Label>
                  <Label>
                    Scale
                    <Input
                      type="number"
                      step="0.05"
                      min={0.01}
                      max={10}
                      value={(form.avatar_config as { scale?: number } | undefined)?.scale ?? 1}
                      onChange={(e) => setAvatar({ scale: Number(e.target.value) })}
                    />
                  </Label>
                </>
              )}
            </div>
          </details>

          <details
            className="config-group rounded-lg border border-border px-2.5 py-1.5"
            open={Object.keys(form.orchestration || {}).length > 0}
          >
            <summary className="cursor-pointer text-sm font-semibold text-muted">Orchestration</summary>
            <div className="mt-2">
              <OrchestrationTab
                value={form.orchestration || {}}
                onChange={(v) => setForm({ ...form, orchestration: v })}
              />
            </div>
          </details>

          {editing !== "new" && (
            <details className="config-group rounded-lg border border-border px-2.5 py-1.5">
              <summary className="cursor-pointer text-sm font-semibold text-muted">
                Harness History
              </summary>
              <div className="mt-2">
                <HarnessHistory agentId={editing} />
              </div>
            </details>
          )}

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
