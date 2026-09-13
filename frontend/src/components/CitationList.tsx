import { AlertTriangle } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type KbChunkDetail, type RagInfo } from "../api/client";
import { cn } from "@/lib/utils";

/** Citation chips under a RAG-grounded assistant answer (SPEC §14.6, Phase 12e).
 *  Click a chip to expand the exact source chunk. */
export default function CitationList({ rag }: { rag: RagInfo }) {
  const navigate = useNavigate();
  const [open, setOpen] = useState<string | null>(null);
  const [detail, setDetail] = useState<KbChunkDetail | null>(null);
  const [loading, setLoading] = useState(false);

  function openInPlayground(queryLogId: string) {
    navigate(`/settings/playground?log=${encodeURIComponent(queryLogId)}`);
  }

  if (rag.no_context) {
    return (
      <div className="citations flex items-center gap-1 text-xs text-muted">
        <AlertTriangle className="size-3.5 shrink-0" />
        <span>
          Not found in the knowledge base
          {rag.reason ? ` (${rag.reason})` : ""} — the answer is not grounded in your documents.
          {rag.query_log_id && (
            <>
              {" · "}
              <button
                className="linkish cursor-pointer border-none bg-transparent px-1 py-0 text-2xs text-accent underline"
                onClick={() => openInPlayground(rag.query_log_id!)}
              >
                trace {rag.query_log_id.slice(0, 8)}
              </button>
            </>
          )}
        </span>
      </div>
    );
  }
  if (!rag.citations.length) return null;

  async function toggle(chunkId: string) {
    if (open === chunkId) {
      setOpen(null);
      return;
    }
    setOpen(chunkId);
    setDetail(null);
    setLoading(true);
    try {
      setDetail(await api.kbChunk(chunkId));
    } catch {
      setDetail(null);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="citations mt-2 flex flex-wrap items-center gap-1.5">
      <span className="text-xs text-muted">Sources:</span>
      {rag.citations.map((c, i) => (
        <button
          key={c.chunk_id}
          className={cn(
            "citation-chip cursor-pointer rounded-full border border-border bg-bg-alt px-2.5 py-0.5 text-xs text-text",
            open === c.chunk_id && "active border-accent text-accent",
          )}
          onClick={() => toggle(c.chunk_id)}
          title={`${c.source_uri}${c.score != null ? ` · score ${c.score.toFixed(3)}` : ""}`}
        >
          [{i + 1}] {c.title}
          {c.score != null && <span className="text-muted"> {c.score.toFixed(2)}</span>}
        </button>
      ))}
      {rag.query_log_id && (
        <button
          className="linkish cursor-pointer border-none bg-transparent px-1 py-0 text-2xs text-accent underline"
          onClick={() => openInPlayground(rag.query_log_id!)}
          title="Open this retrieval in the Playground"
        >
          trace
        </button>
      )}
      {open && (
        <div className="citation-detail mt-1.5 basis-full rounded-lg border border-border p-2.5">
          {loading && <span className="text-xs text-muted">loading…</span>}
          {detail && (
            <>
              <div className="text-xs text-muted">
                {detail.document?.title} · {detail.document?.source_uri} · chunk #{detail.ordinal}
              </div>
              <div className="citation-text mt-1 max-h-80 overflow-auto whitespace-pre-wrap text-sm">
                {detail.text}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
