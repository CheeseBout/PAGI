import { useCallback, useEffect, useState } from "react";
import {
  api,
  type KbCollection,
  type KbEvalCase,
  type KbEvalCaseInput,
  type KbEvalRun,
} from "../../api/client";
import { ConfigForm } from "./ConfigForm";
import { selectCls } from "./formStyles";
import { Button } from "@/components/ui/button";
import { ErrorBanner } from "@/components/ui/error-banner";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

const METRICS = [
  ["faithfulness", 0.85],
  ["answer_relevancy", 0.8],
  ["context_precision", 0.7],
  ["context_recall", 0.75],
] as const;

function score(v: number | null, min: number) {
  if (v === null) return <span className="text-muted">–</span>;
  return <span className={v >= min ? "font-semibold text-success" : "font-semibold text-danger"}>{v.toFixed(3)}</span>;
}

/** Golden-set evaluation (SPEC §14.8, PLAN §12f). Generate a set, run it against
 *  a config, compare runs. */
export default function EvalTab() {
  const [collections, setCollections] = useState<KbCollection[]>([]);
  const [cid, setCid] = useState("");
  const [n, setN] = useState(20);
  const [cases, setCases] = useState<KbEvalCaseInput[]>([]);
  const [cfgObj, setCfgObj] = useState<Record<string, unknown>>({});
  const [runName, setRunName] = useState("");
  const [runs, setRuns] = useState<KbEvalRun[]>([]);
  const [open, setOpen] = useState<{ run: KbEvalRun; cases: KbEvalCase[] } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    api.listKbCollections().then((cs) => {
      setCollections(cs);
      if (cs[0] && !cid) setCid(cs[0].id);
    });
  }, [cid]);

  const reloadRuns = useCallback(() => {
    if (cid) api.kbEvalRuns(cid).then(setRuns).catch(() => {});
  }, [cid]);
  useEffect(() => {
    reloadRuns();
  }, [reloadRuns]);

  // poll while any run is still going
  useEffect(() => {
    if (!runs.some((r) => r.status === "running")) return;
    const t = setInterval(reloadRuns, 2000);
    return () => clearInterval(t);
  }, [runs, reloadRuns]);

  const wrap = async (tag: string, fn: () => Promise<void>) => {
    setErr(null);
    setBusy(tag);
    try {
      await fn();
    } catch (e) {
      setErr((e as Error)?.message || "failed");
    } finally {
      setBusy(null);
    }
  };

  const genSet = () =>
    wrap("gen", async () => {
      const { cases: c } = await api.kbEvalGenerate(cid, n);
      setCases(c);
    });

  const runEval = () =>
    wrap("run", async () => {
      if (!cases.length) throw new Error("generate or add cases first");
      await api.kbCreateEvalRun({
        collection_id: cid,
        name: runName || `run ${new Date().toISOString().slice(0, 16)}`,
        cases,
        config: Object.keys(cfgObj).length ? cfgObj : undefined,
      });
      setRunName("");
      reloadRuns();
    });

  return (
    <div className="settings-section flex flex-col gap-3">
      <div className="settings-section-head flex items-center justify-between">
        <h2 className="text-lg font-semibold">Evaluation</h2>
        <select className={selectCls} value={cid} onChange={(e) => setCid(e.target.value)}>
          {collections.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
      </div>
      <ErrorBanner message={err} />

      <div className="form-row flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-2xs text-muted">
          Golden set size
          <Input
            type="number"
            value={n}
            min={4}
            max={200}
            onChange={(e) => setN(Number(e.target.value))}
          />
        </label>
        <Button disabled={!cid || busy === "gen"} onClick={genSet}>
          {busy === "gen" ? "Generating…" : "Generate golden set"}
        </Button>
      </div>

      {cases.length > 0 && (
        <>
          <p className="text-xs text-muted">{cases.length} cases — edit before running if you like.</p>
          <div className="overflow-x-auto">
          <table className="settings-table w-full border-collapse text-sm [&_td]:border-b [&_td]:border-border [&_td]:px-2.5 [&_td]:py-2 [&_td]:align-middle [&_th]:border-b [&_th]:border-border [&_th]:px-2.5 [&_th]:py-2 [&_th]:text-left [&_th]:font-semibold [&_th]:text-muted">
            <thead>
              <tr>
                <th>Question</th>
                <th>Ground truth</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {cases.map((c, i) => (
                <tr key={i}>
                  <td>
                    <Input
                      value={c.question}
                      onChange={(e) =>
                        setCases((cs) =>
                          cs.map((x, j) => (j === i ? { ...x, question: e.target.value } : x)),
                        )
                      }
                    />
                  </td>
                  <td>
                    <Input
                      value={c.ground_truth ?? ""}
                      onChange={(e) =>
                        setCases((cs) =>
                          cs.map((x, j) =>
                            j === i ? { ...x, ground_truth: e.target.value || null } : x,
                          ),
                        )
                      }
                    />
                  </td>
                  <td className="row-actions text-right">
                    <Button
                      variant="destructive"
                      size="sm"
                      onClick={() => setCases((cs) => cs.filter((_, j) => j !== i))}
                    >
                      ×
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>

          <details className="config-group rounded-lg border border-border px-2.5 py-1.5">
            <summary className="cursor-pointer text-sm text-muted">
              Config override for this run (SPEC §14.2)
            </summary>
            <ConfigForm which="rag" value={cfgObj} onChange={setCfgObj} />
          </details>
          <div className="form-row flex flex-wrap items-end gap-3">
            <label className="flex flex-1 flex-col gap-1 text-2xs text-muted">
              Run name
              <Input value={runName} onChange={(e) => setRunName(e.target.value)} />
            </label>
            <Button disabled={busy === "run"} onClick={runEval}>
              {busy === "run" ? "Starting…" : "Run eval"}
            </Button>
          </div>
        </>
      )}

      <h3 className="mb-0">Runs</h3>
      <div className="overflow-x-auto">
      <table className="settings-table w-full border-collapse text-sm [&_td]:border-b [&_td]:border-border [&_td]:px-2.5 [&_td]:py-2 [&_td]:align-middle [&_th]:border-b [&_th]:border-border [&_th]:px-2.5 [&_th]:py-2 [&_th]:text-left [&_th]:font-semibold [&_th]:text-muted">
        <thead>
          <tr>
            <th>Name</th>
            <th>Cases</th>
            <th>Faith.</th>
            <th>Ans.rel.</th>
            <th>Ctx.prec.</th>
            <th>Ctx.rec.</th>
            <th>Status</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.id} className={cn(open?.run.id === r.id && "active-row bg-bg-alt")}>
              <td>{r.name}</td>
              <td>{r.case_count}</td>
              {METRICS.map(([m, min]) => (
                <td key={m}>{score(r[m], min)}</td>
              ))}
              <td>{r.status}</td>
              <td className="row-actions flex justify-end gap-1.5">
                <Button variant="outline" size="sm" onClick={() => api.kbEvalRun(r.id).then(setOpen)}>
                  Cases
                </Button>
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={() =>
                    wrap("del", async () => {
                      await api.kbDeleteEvalRun(r.id);
                      if (open?.run.id === r.id) setOpen(null);
                      reloadRuns();
                    })
                  }
                >
                  Delete
                </Button>
              </td>
            </tr>
          ))}
          {runs.length === 0 && (
            <tr>
              <td colSpan={8} className="text-muted">
                No runs yet.
              </td>
            </tr>
          )}
        </tbody>
      </table>
      </div>

      {open && (
        <div className="settings-form rounded-lg border border-border bg-bg-elev p-4">
          <h3 className="mt-0">
            {open.run.name}{" "}
            <span className="text-xs text-muted">
              config: {Object.entries(open.run.config_snapshot)
                .filter(([k]) =>
                  ["retrieval_mode", "rerank_mode", "top_k_dense", "hybrid_alpha", "chunk_strategy"]
                    .includes(k))
                .map(([k, v]) => `${k}=${v}`)
                .join(" · ")}
            </span>
          </h3>
          <div className="overflow-x-auto">
          <table className="settings-table w-full border-collapse text-sm [&_td]:border-b [&_td]:border-border [&_td]:px-2.5 [&_td]:py-2 [&_td]:align-middle [&_th]:border-b [&_th]:border-border [&_th]:px-2.5 [&_th]:py-2 [&_th]:text-left [&_th]:font-semibold [&_th]:text-muted">
            <thead>
              <tr>
                <th>Question</th>
                <th>Faith.</th>
                <th>Ans.</th>
                <th>Prec.</th>
                <th>Rec.</th>
              </tr>
            </thead>
            <tbody>
              {open.cases.map((c) => (
                <tr key={c.id}>
                  <td title={c.answer ?? ""}>{c.question.slice(0, 70)}</td>
                  {METRICS.map(([m, min]) => (
                    <td key={m}>{score(c[m], min)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        </div>
      )}
    </div>
  );
}
