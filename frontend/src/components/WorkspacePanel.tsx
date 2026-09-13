import { ArrowUp, File, Folder, RefreshCw, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api, type WorkspaceEntry } from "../api/client";
import { confirmAction } from "@/store/confirmStore";
import { Button } from "@/components/ui/button";
import { ErrorBanner } from "@/components/ui/error-banner";

function joinPath(dir: string, name: string): string {
  return dir ? `${dir}/${name}` : name;
}

/** The sandbox file browser — content only, no panel chrome. Rendered inside
 * <RightPanel>'s "Files" tab, which owns the shared close button/tab strip. */
export default function WorkspacePanel({
  sessionId,
  refreshKey,
}: {
  sessionId: string;
  /** bump to force a reload (e.g. after a file tool runs) */
  refreshKey: number;
}) {
  const [dir, setDir] = useState("");
  const [entries, setEntries] = useState<WorkspaceEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [viewing, setViewing] = useState<{ path: string; content: string; truncated: boolean } | null>(
    null,
  );

  const load = useCallback(
    (target: string) => {
      setLoading(true);
      setError(null);
      api
        .listWorkspace(sessionId, target || undefined)
        .then((r) => {
          setEntries(r.entries);
          setDir(target);
        })
        .catch((e) => setError(e?.message || "Could not read the workspace"))
        .finally(() => setLoading(false));
    },
    [sessionId],
  );

  useEffect(() => {
    load(dir);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, refreshKey]);

  function open(entry: WorkspaceEntry) {
    const path = joinPath(dir, entry.name);
    if (entry.type === "dir") {
      load(path);
      return;
    }
    api
      .readWorkspaceFile(sessionId, path)
      .then((r) => setViewing({ path, content: r.content, truncated: r.truncated }))
      .catch((e) => setError(e?.message || "Could not read the file"));
  }

  function up() {
    load(dir.includes("/") ? dir.slice(0, dir.lastIndexOf("/")) : "");
  }

  async function remove(entry: WorkspaceEntry) {
    const ok = await confirmAction({
      title: `Delete "${entry.name}"?`,
      description: "This removes the file from the sandbox workspace. This can't be undone.",
      confirmLabel: "Delete",
      variant: "destructive",
    });
    if (!ok) return;
    try {
      await api.deleteWorkspaceFile(sessionId, joinPath(dir, entry.name));
      load(dir);
    } catch (e) {
      setError((e as Error)?.message || "Delete failed");
    }
  }

  return (
    <div className="workspace-panel flex flex-col gap-2 p-2.5">
      <div className="workspace-path flex items-center gap-2 text-sm">
        <Button variant="ghost" size="icon" className="size-7" onClick={up} disabled={!dir}>
          <ArrowUp className="size-3.5" />
        </Button>
        <code className="flex-1 overflow-hidden text-ellipsis whitespace-nowrap">/{dir}</code>
        <Button variant="ghost" size="icon" className="size-7" onClick={() => load(dir)} title="Refresh">
          <RefreshCw className="size-3.5" />
        </Button>
      </div>

      <ErrorBanner message={error} />
      {loading && <div className="p-3 text-muted">Loading…</div>}

      {!loading && (
        <ul className="workspace-list m-0 flex list-none flex-col gap-0.5 p-0">
          {entries.length === 0 && <li className="text-muted">empty</li>}
          {entries.map((e) => (
            <li key={e.name} className="workspace-item flex items-center gap-1.5 text-sm">
              <button
                className="workspace-name flex flex-1 items-center gap-1.5 overflow-hidden text-ellipsis whitespace-nowrap rounded-md border-none bg-transparent px-1.5 py-1 text-left hover:bg-bg-elev"
                onClick={() => open(e)}
              >
                {e.type === "dir" ? <Folder className="size-3.5 shrink-0" /> : <File className="size-3.5 shrink-0" />}{" "}
                {e.name}
              </button>
              <span className="workspace-size text-xs text-muted">
                {e.type === "file" ? `${e.size_bytes} B` : ""}
              </span>
              <button
                className="workspace-del border-none bg-transparent px-1 text-muted hover:text-danger"
                title="Delete"
                onClick={() => remove(e)}
              >
                <Trash2 className="size-3.5" />
              </button>
            </li>
          ))}
        </ul>
      )}

      {viewing && (
        <div className="workspace-viewer mt-2 border-t border-border pt-2">
          <div className="workspace-viewer-head flex items-center justify-between text-xs">
            <code>{viewing.path}</code>
            <button onClick={() => setViewing(null)} className="text-muted hover:text-text">
              <X className="size-3.5" />
            </button>
          </div>
          <pre className="workspace-viewer-body mt-1 max-h-80 overflow-auto whitespace-pre-wrap rounded-lg bg-code-bg p-2.5 text-xs text-code-fg">
            {viewing.content}
            {viewing.truncated && "\n\n… (truncated)"}
          </pre>
        </div>
      )}
    </div>
  );
}
