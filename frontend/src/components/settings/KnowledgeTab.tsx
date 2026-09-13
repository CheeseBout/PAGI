import { AlertTriangle } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  type KbCollection,
  type KbDocument,
  type KbSearchResult,
} from "../../api/client";
import { ConfigForm } from "./ConfigForm";
import { useErr } from "./shared";
import { confirmAction } from "@/store/confirmStore";
import { Button } from "@/components/ui/button";
import { ErrorBanner } from "@/components/ui/error-banner";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

const BUSY = new Set(["pending", "parsing", "chunking", "enriching", "embedding"]);

const tableCls =
  "settings-table w-full border-collapse text-sm [&_td]:border-b [&_td]:border-border [&_td]:px-2.5 [&_td]:py-2 [&_td]:align-middle [&_th]:border-b [&_th]:border-border [&_th]:px-2.5 [&_th]:py-2 [&_th]:text-left [&_th]:font-semibold [&_th]:text-muted";

export default function KnowledgeTab() {
  const { err, wrap } = useErr();
  const [collections, setCollections] = useState<KbCollection[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [newName, setNewName] = useState("");

  const reload = useCallback(() => api.listKbCollections().then(setCollections), []);
  useEffect(() => {
    reload();
  }, [reload]);

  const create = () =>
    wrap(async () => {
      if (!newName.trim()) return;
      const c = await api.createKbCollection({ name: newName.trim() });
      setNewName("");
      await reload();
      setSelected(c.id);
    });

  return (
    <div className="settings-section flex flex-col gap-3">
      <div className="settings-section-head mb-3 flex items-center justify-between">
        <h2 className="text-lg font-semibold">Knowledge base</h2>
      </div>
      <ErrorBanner message={err} />

      <div className="form-row flex flex-wrap items-end gap-3">
        <label className="flex flex-1 flex-col gap-1 text-2xs text-muted">
          New collection name
          <Input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="e.g. Company policies"
            onKeyDown={(e) => e.key === "Enter" && create()}
          />
        </label>
        <Button onClick={create}>
          + Create
        </Button>
      </div>

      <div className="overflow-x-auto">
      <table className={tableCls}>
        <thead>
          <tr>
            <th>Name</th>
            <th>Docs</th>
            <th>Chunks</th>
            <th>Embedding model</th>
            <th>Status</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {collections.map((c) => (
            <tr key={c.id} className={cn(selected === c.id && "active-row bg-bg-alt")}>
              <td>{c.name}</td>
              <td>{c.doc_count}</td>
              <td>{c.chunk_count}</td>
              <td>
                <code className="text-xs">{c.embedding_model}</code>
              </td>
              <td>{c.status}</td>
              <td className="row-actions flex justify-end gap-1.5">
                <Button variant="outline" size="sm" onClick={() => setSelected(selected === c.id ? null : c.id)}>
                  {selected === c.id ? "Close" : "Open"}
                </Button>
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={() =>
                    wrap(async () => {
                      const ok = await confirmAction({
                        title: `Delete collection "${c.name}"?`,
                        description: "This permanently deletes all its documents and chunks.",
                        confirmLabel: "Delete",
                        variant: "destructive",
                      });
                      if (!ok) return;
                      await api.deleteKbCollection(c.id);
                      if (selected === c.id) setSelected(null);
                      await reload();
                    })
                  }
                >
                  Delete
                </Button>
              </td>
            </tr>
          ))}
          {collections.length === 0 && (
            <tr>
              <td colSpan={6} className="text-muted">
                No collections yet.
              </td>
            </tr>
          )}
        </tbody>
      </table>
      </div>

      {selected && (
        <CollectionDetail
          key={selected}
          collectionId={selected}
          onChange={reload}
        />
      )}
    </div>
  );
}

function CollectionDetail({
  collectionId,
  onChange,
}: {
  collectionId: string;
  onChange: () => void;
}) {
  const { err, wrap } = useErr();
  const [docs, setDocs] = useState<KbDocument[]>([]);
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [url, setUrl] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const [search, setSearch] = useState("");
  const [result, setResult] = useState<KbSearchResult | null>(null);
  const [cfgObj, setCfgObj] = useState<Record<string, unknown>>({});
  const [cfgSaved, setCfgSaved] = useState<Record<string, unknown>>({});
  const [showCfg, setShowCfg] = useState(false);

  const reload = useCallback(
    () => api.listKbDocuments(collectionId).then(setDocs),
    [collectionId],
  );
  const reloadCfg = useCallback(
    () =>
      api.getKbCollection(collectionId).then((c) => {
        setCfgObj(c.config ?? {});
        setCfgSaved(c.config ?? {});
      }),
    [collectionId],
  );
  useEffect(() => {
    reload();
    reloadCfg();
  }, [reload, reloadCfg]);

  // poll while anything is still ingesting
  useEffect(() => {
    if (!docs.some((d) => BUSY.has(d.status))) return;
    const t = setInterval(() => {
      reload();
      onChange();
    }, 1500);
    return () => clearInterval(t);
  }, [docs, reload, onChange]);

  const afterAdd = async () => {
    await reload();
    onChange();
  };

  const saveCfg = () =>
    wrap(async () => {
      await api.updateKbCollection(collectionId, { config: cfgObj });
      await reloadCfg();
    });

  const dirty = JSON.stringify(cfgObj) !== JSON.stringify(cfgSaved);

  return (
    <div className="settings-form flex flex-col gap-3 rounded-lg border border-border bg-bg-elev p-4">
      <h3 className="mt-0 flex items-center gap-2">
        Retrieval config{" "}
        <Button
          variant="link"
          size="sm"
          className="linkish h-auto px-1 py-0 text-2xs underline"
          onClick={() => setShowCfg((s) => !s)}
        >
          {showCfg ? "hide" : "edit"}
        </Button>
      </h3>
      {showCfg && (
        <>
          <ConfigForm which="rag" value={cfgObj} onChange={setCfgObj} />
          <div className="form-actions flex gap-2">
            <Button onClick={saveCfg} disabled={!dirty}>
              {dirty ? "Save config" : "Saved"}
            </Button>
          </div>
        </>
      )}

      <h3>Documents</h3>
      <ErrorBanner message={err} />

      <div className="form-row flex flex-wrap items-end gap-3">
        <label className="flex flex-1 flex-col gap-1 text-2xs text-muted">
          Paste text — title
          <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Title" />
        </label>
      </div>
      <label className="flex flex-col gap-1 text-2xs text-muted">
        Text
        <Textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} />
      </label>
      <div className="form-actions flex gap-2">
        <Button
          variant="outline"
          onClick={() =>
            wrap(async () => {
              if (!title.trim() || !text.trim()) return;
              await api.addKbText(collectionId, title.trim(), text);
              setTitle("");
              setText("");
              await afterAdd();
            })
          }
        >
          Add text
        </Button>
      </div>

      <div className="form-row flex flex-wrap items-end gap-3">
        <label className="flex flex-1 flex-col gap-1 text-2xs text-muted">
          …or ingest a URL
          <Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…" />
        </label>
        <Button
          variant="outline"
          onClick={() =>
            wrap(async () => {
              if (!url.trim()) return;
              await api.addKbUrl(collectionId, url.trim());
              setUrl("");
              await afterAdd();
            })
          }
        >
          Add URL
        </Button>
      </div>

      <div className="form-row flex flex-wrap items-end gap-3">
        <label className="flex flex-1 flex-col gap-1 text-2xs text-muted">
          …or upload a file (PDF, Markdown, text, HTML, JSON)
          <input
            ref={fileRef}
            type="file"
            accept=".pdf,.md,.markdown,.txt,.html,.htm,.json"
            className="text-sm"
          />
        </label>
        <Button
          variant="outline"
          onClick={() =>
            wrap(async () => {
              const f = fileRef.current?.files?.[0];
              if (!f) return;
              await api.uploadKbDocument(collectionId, f);
              if (fileRef.current) fileRef.current.value = "";
              await afterAdd();
            })
          }
        >
          Upload
        </Button>
      </div>

      <div className="overflow-x-auto">
      <table className={tableCls}>
        <thead>
          <tr>
            <th>Title</th>
            <th>Source</th>
            <th>Status</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {docs.map((d) => (
            <tr key={d.id}>
              <td>
                {d.title}
                {d.injection_flagged && (
                  <span
                    className="ml-1 inline-flex items-center gap-1 text-xs text-muted"
                    title="prompt-injection markers found — review"
                  >
                    <AlertTriangle className="size-3" /> injection?
                  </span>
                )}
              </td>
              <td className="text-xs text-muted">{d.source_type}</td>
              <td>
                {d.status}
                {d.error && <div className="text-xs text-muted">{d.error}</div>}
              </td>
              <td className="row-actions flex justify-end gap-1.5">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() =>
                    wrap(async () => {
                      await api.reingestKbDocument(d.id);
                      await afterAdd();
                    })
                  }
                >
                  Re-ingest
                </Button>
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={() =>
                    wrap(async () => {
                      await api.deleteKbDocument(d.id);
                      await afterAdd();
                    })
                  }
                >
                  Delete
                </Button>
              </td>
            </tr>
          ))}
          {docs.length === 0 && (
            <tr>
              <td colSpan={4} className="text-muted">
                No documents yet.
              </td>
            </tr>
          )}
        </tbody>
      </table>
      </div>

      <h3>Quick retrieval test</h3>
      <div className="form-row flex flex-wrap items-end gap-3">
        <label className="flex flex-1 flex-col gap-1 text-2xs text-muted">
          Query
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) =>
              e.key === "Enter" &&
              wrap(async () => {
                setResult(await api.kbSearch(search, [collectionId], true));
              })
            }
          />
        </label>
        <Button
          variant="outline"
          onClick={() =>
            wrap(async () => {
              setResult(await api.kbSearch(search, [collectionId], true));
            })
          }
        >
          Search
        </Button>
      </div>
      {result && (
        <div className="rag-result flex flex-col gap-2">
          <div className="text-xs text-muted">
            {result.no_context
              ? `no context (${result.reason})`
              : `${result.chunks.length} chunk(s) · ${result.total_ms} ms`}
            {result.stages && (
              <>
                {" · "}
                {result.stages
                  .map((s) => `${s.name}${s.skipped ? "—" : `:${s.duration_ms}ms`}`)
                  .join(" ")}
              </>
            )}
          </div>
          {result.chunks.map((c) => (
            <div key={c.chunk_id} className="rag-chunk rounded-lg border border-border p-2 text-sm">
              <div className="mb-1 text-xs text-muted">
                {c.title} · score {c.score.toFixed(3)}
              </div>
              <div className="whitespace-pre-wrap">{c.text.slice(0, 400)}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
