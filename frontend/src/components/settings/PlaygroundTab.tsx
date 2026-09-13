import { useCallback, useEffect, useState } from "react";
import {
  api,
  type KbChunkDetail,
  type KbCollection,
  type KbQueryLog,
  type KbSearchResult,
  type KbStage,
} from "../../api/client";
import { ConfigForm } from "./ConfigForm";
import { checkboxCls } from "./formStyles";
import { Button } from "@/components/ui/button";
import { ErrorBanner } from "@/components/ui/error-banner";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

/** Retrieval inspector (SPEC §14.4/§14.7, PLAN §12e). Run a query and see every
 *  pipeline stage with timings + candidates; or open a past query log to trace a
 *  bad answer back to the stage that produced it. */
export default function PlaygroundTab({
  initialQueryLogId,
}: {
  /** Set when arriving via a citation's "trace" link (CitationList). */
  initialQueryLogId?: string | null;
}) {
  const [collections, setCollections] = useState<KbCollection[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [cfgObj, setCfgObj] = useState<Record<string, unknown>>({});
  const [result, setResult] = useState<KbSearchResult | null>(null);
  const [logs, setLogs] = useState<KbQueryLog[]>([]);
  const [openLog, setOpenLog] = useState<KbQueryLog | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const reloadLogs = useCallback(() => api.kbQueryLogs().then(setLogs).catch(() => {}), []);
  useEffect(() => {
    api.listKbCollections().then(setCollections).catch(() => {});
    reloadLogs();
  }, [reloadLogs]);

  useEffect(() => {
    if (!initialQueryLogId) return;
    api
      .kbQueryLog(initialQueryLogId)
      .then((full) => {
        setOpenLog(full);
        setResult(null);
      })
      .catch(() => setErr("Couldn't load that query log — it may have expired."));
    // Only ever act on the id we arrived with, once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialQueryLogId]);

  const run = async () => {
    setErr(null);
    setBusy(true);
    setOpenLog(null);
    try {
      const config = Object.keys(cfgObj).length ? cfgObj : undefined;
      const r = await api.kbSearch(query, selected, true, config);
      setResult(r);
      reloadLogs();
    } catch (e) {
      setErr((e as Error)?.message || "search failed");
    } finally {
      setBusy(false);
    }
  };

  const toggleColl = (id: string) =>
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));

  return (
    <div className="settings-section flex flex-col gap-3">
      <div className="settings-section-head mb-3 flex items-center justify-between">
        <h2 className="text-lg font-semibold">Retrieval playground</h2>
      </div>
      <ErrorBanner message={err} />

      <fieldset className="tools-fieldset rounded-lg border border-border p-2.5">
        <legend className="px-1.5 text-xs text-muted">Collections</legend>
        {collections.length === 0 && <span className="text-xs text-muted">No collections.</span>}
        {collections.map((c) => (
          <label key={c.id} className="checkbox flex items-center gap-1.5 py-0.5 text-sm">
            <input
              type="checkbox"
              className={checkboxCls}
              checked={selected.includes(c.id)}
              onChange={() => toggleColl(c.id)}
            />
            {c.name} <span className="text-xs text-muted">({c.chunk_count} chunks)</span>
          </label>
        ))}
      </fieldset>

      <label className="flex flex-col gap-1 text-2xs text-muted">
        Query
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && !busy && selected.length && run()}
        />
      </label>
      <details className="config-group rounded-lg border border-border px-2.5 py-1.5">
        <summary className="cursor-pointer text-sm text-muted">
          Config override (applied on top of the collection's, SPEC §14.2)
        </summary>
        <ConfigForm which="rag" value={cfgObj} onChange={setCfgObj} />
      </details>
      <div className="form-actions flex gap-2">
        <Button disabled={busy || !selected.length || !query} onClick={run}>
          {busy ? "Running…" : "Run"}
        </Button>
      </div>

      {result && (
        <StageView
          stages={result.stages || []}
          context={result.context}
          totalMs={result.total_ms}
          noContext={result.no_context}
          reason={result.reason}
          configResolved={result.config_resolved}
          citations={result.chunks.map((c) => ({
            chunk_id: c.chunk_id,
            title: c.title,
            source_uri: c.source_uri,
          }))}
        />
      )}

      <h3 className="mb-0">Recent query logs</h3>
      <div className="overflow-x-auto">
      <table className="settings-table w-full border-collapse text-sm [&_td]:border-b [&_td]:border-border [&_td]:px-2.5 [&_td]:py-2 [&_td]:align-middle [&_th]:border-b [&_th]:border-border [&_th]:px-2.5 [&_th]:py-2 [&_th]:text-left [&_th]:font-semibold [&_th]:text-muted">
        <thead>
          <tr>
            <th>When</th>
            <th>Query</th>
            <th>Picked</th>
            <th>ms</th>
            <th>Source</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {logs.map((l) => (
            <tr key={l.id} className={l.id === openLog?.id ? "active-row bg-bg-alt" : ""}>
              <td className="text-xs text-muted">{l.created_at.slice(0, 19).replace("T", " ")}</td>
              <td>{l.query_raw.slice(0, 60)}</td>
              <td>{l.picked_chunk_ids.length}</td>
              <td>{l.total_ms}</td>
              <td className="text-xs text-muted">{l.session_id ? "chat" : "playground"}</td>
              <td className="row-actions text-right">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() =>
                    api.kbQueryLog(l.id).then((full) => {
                      setOpenLog(full);
                      setResult(null);
                    })
                  }
                >
                  Trace
                </Button>
              </td>
            </tr>
          ))}
          {logs.length === 0 && (
            <tr>
              <td colSpan={6} className="text-muted">
                No query logs yet.
              </td>
            </tr>
          )}
        </tbody>
      </table>
      </div>

      {openLog && (
        <StageView
          title={`Trace · "${openLog.query_raw}"`}
          stages={openLog.stages}
          totalMs={openLog.total_ms}
          noContext={openLog.picked_chunk_ids.length === 0}
          configResolved={openLog.config_snapshot}
          pickedIds={openLog.picked_chunk_ids}
        />
      )}
    </div>
  );
}

function StageView({
  title,
  stages,
  context,
  totalMs,
  noContext,
  reason,
  configResolved,
  citations,
  pickedIds,
}: {
  title?: string;
  stages: KbStage[];
  context?: string;
  totalMs: number;
  noContext?: boolean;
  reason?: string | null;
  configResolved?: Record<string, unknown>;
  citations?: { chunk_id: string; title: string; source_uri: string }[];
  pickedIds?: string[];
}) {
  const [chunk, setChunk] = useState<KbChunkDetail | null>(null);
  const [chunkOpen, setChunkOpen] = useState<string | null>(null);

  const showChunk = async (id: string) => {
    if (chunkOpen === id) {
      setChunkOpen(null);
      return;
    }
    setChunkOpen(id);
    setChunk(null);
    try {
      setChunk(await api.kbChunk(id));
    } catch {
      setChunk(null);
    }
  };

  const maxStageMs = Math.max(0, ...stages.filter((s) => !s.skipped).map((s) => s.duration_ms));

  return (
    <div className="playground-result my-3.5 rounded-md border border-border p-3.5">
      <h3 className="mt-0">
        {title || "Pipeline"} · {totalMs} ms
      </h3>
      {noContext && (
        <div className="text-muted">No context returned{reason ? ` — ${reason}` : ""}.</div>
      )}
      <ol className="stage-list my-2 flex flex-col gap-1 p-0">
        {stages.map((s, i) => (
          <li
            key={i}
            className={cn(
              "stage relative overflow-hidden rounded-r-md border-l-[3px] border-accent bg-bg-alt px-2 py-1",
              s.skipped && "skipped border-l-border opacity-55",
            )}
          >
            {!s.skipped && maxStageMs > 0 && (
              <span
                aria-hidden="true"
                className="absolute inset-y-0 left-0 bg-accent/10"
                style={{ width: `${(s.duration_ms / maxStageMs) * 100}%` }}
              />
            )}
            <div className="relative">
              <span className="stage-name mr-2 font-semibold">{s.name}</span>
              <span className="text-xs text-muted">
                {s.skipped ? "skipped" : `${s.duration_ms} ms`}
                {typeof s.candidate_count === "number" ? ` · ${s.candidate_count} cand` : ""}
                {s.method ? ` · ${s.method}` : ""}
                {s.mode ? ` · ${s.mode}` : ""}
                {s.verdict ? ` · ${s.verdict}` : ""}
                {typeof s.score === "number" ? ` (${s.score})` : ""}
                {s.fallback && s.fallback !== "none" ? ` · fallback=${s.fallback}` : ""}
                {typeof s.removed === "number" ? ` · −${s.removed}` : ""}
                {s.detail && s.detail !== "n/a" ? ` · ${s.detail}` : ""}
                {typeof s.context_tokens === "number" ? ` · ${s.context_tokens} tok` : ""}
              </span>
              {s.queries && s.queries.length > 1 && (
                <div className="stage-top mt-1 flex flex-wrap gap-1.5 text-xs text-muted">
                  {s.queries.map((q, j) => (
                    <div key={j}>
                      <code>{q.kind}</code> {q.text.slice(0, 90)}
                    </div>
                  ))}
                </div>
              )}
              {s.top && s.top.length > 0 && (
                <div className="stage-top mt-1 flex flex-wrap gap-1.5 text-xs text-muted">
                  {s.top.slice(0, 5).map((t) => (
                    <code key={t.chunk_id}>
                      {t.chunk_id.slice(0, 6)}:{t.score.toFixed(3)}
                    </code>
                  ))}
                </div>
              )}
            </div>
          </li>
        ))}
      </ol>

      {citations && citations.length > 0 && (
        <div className="citations mt-2 flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-muted">Final passages:</span>
          {citations.map((c, i) => (
            <button
              key={c.chunk_id}
              className={cn(
                "citation-chip rounded-full border border-border bg-bg-alt px-2.5 py-0.5 text-xs",
                chunkOpen === c.chunk_id && "active border-accent text-accent",
              )}
              onClick={() => showChunk(c.chunk_id)}
            >
              [{i + 1}] {c.title}
            </button>
          ))}
        </div>
      )}
      {pickedIds && pickedIds.length > 0 && (
        <div className="citations mt-2 flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-muted">Picked chunk ids:</span>
          {pickedIds.map((id, i) => (
            <button
              key={id}
              className={cn(
                "citation-chip rounded-full border border-border bg-bg-alt px-2.5 py-0.5 text-xs",
                chunkOpen === id && "active border-accent text-accent",
              )}
              onClick={() => showChunk(id)}
            >
              [{i + 1}] {id.slice(0, 8)}
            </button>
          ))}
        </div>
      )}
      {chunkOpen && chunk && (
        <div className="citation-detail mt-1.5 rounded-lg border border-border p-2.5">
          <div className="text-xs text-muted">
            {chunk.document?.title} · {chunk.document?.source_uri} · #{chunk.ordinal}
          </div>
          <div className="citation-text mt-1 whitespace-pre-wrap text-sm">{chunk.text}</div>
        </div>
      )}

      {context && (
        <details className="ctx-details mt-2.5">
          <summary className="cursor-pointer text-sm text-muted">
            Final packed context ({context.length} chars)
          </summary>
          <pre className="citation-text whitespace-pre-wrap text-sm">{context}</pre>
        </details>
      )}
      {configResolved && (
        <details className="ctx-details mt-2.5">
          <summary className="cursor-pointer text-sm text-muted">Config snapshot</summary>
          <pre className="citation-text whitespace-pre-wrap font-mono text-sm">
            {JSON.stringify(configResolved, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
}
