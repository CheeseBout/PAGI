import { useEffect, useMemo, useState } from "react";
import { api, type BudgetReport, type UsageReport } from "../../api/client";
import { ErrorBanner } from "@/components/ui/error-banner";
import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";

const RANGES: Record<string, number> = { "7d": 7, "30d": 30, "90d": 90 };

function sinceIso(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  d.setHours(0, 0, 0, 0);
  return d.toISOString();
}

const selectCls =
  "h-8 rounded-lg border border-border bg-bg px-2 text-sm text-text focus-visible:outline-none focus-visible:border-accent";

export default function UsageTab() {
  const [groupBy, setGroupBy] = useState<"day" | "model" | "session" | "kind">("day");
  const [range, setRange] = useState<keyof typeof RANGES>("30d");
  const [report, setReport] = useState<UsageReport | null>(null);
  const [budget, setBudget] = useState<BudgetReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setError(null);
    api
      .usage(groupBy, sinceIso(RANGES[range]))
      .then(setReport)
      .catch((e) => setError(e?.message || "Could not load usage"));
  }, [groupBy, range]);

  useEffect(() => {
    api.usageBudget().then(setBudget).catch(() => {});
  }, []);

  const maxCost = Math.max(1e-9, ...(report?.buckets.map((b) => b.cost_usd) ?? [0]));

  return (
    <div className="settings-section">
      <div className="settings-section-head flex items-center justify-between">
        <h2 className="text-lg font-semibold">Usage &amp; cost</h2>
        <div className="flex gap-2">
          <select
            className={selectCls}
            value={range}
            onChange={(e) => setRange(e.target.value as keyof typeof RANGES)}
          >
            {Object.keys(RANGES).map((r) => (
              <option key={r}>{r}</option>
            ))}
          </select>
          <select
            className={selectCls}
            value={groupBy}
            onChange={(e) => setGroupBy(e.target.value as typeof groupBy)}
          >
            <option value="day">by day</option>
            <option value="model">by model</option>
            <option value="session">by conversation</option>
            <option value="kind">by kind (chat / embedding / …)</option>
          </select>
        </div>
      </div>
      <ErrorBanner message={error} />

      {budget && (
        <div
          className={cn(
            "mb-4 rounded-lg border border-border p-3",
            budget.over && "border-danger",
          )}
        >
          <div className="text-sm">
            {budget.month}: <strong>${budget.spent_usd.toFixed(4)}</strong>
            {budget.budget_usd > 0 ? ` / $${budget.budget_usd.toFixed(2)} (${budget.pct}%)` : " spent"}
          </div>
          {budget.budget_usd > 0 && (
            <Progress
              value={Math.min(100, budget.pct ?? 0)}
              className={cn("mt-1.5", budget.over && "[&>div]:bg-danger")}
            />
          )}
        </div>
      )}

      {report && (
        <>
          <div className="mb-3.5 flex flex-wrap gap-4 text-sm">
            <span>
              <strong>{report.totals.calls}</strong> calls
            </span>
            <span>
              <strong>${report.totals.cost_usd.toFixed(4)}</strong> cost
            </span>
            <span>
              {report.totals.tokens_in.toLocaleString()} in / {report.totals.tokens_out.toLocaleString()} out
            </span>
            <span>{report.totals.cached_tokens.toLocaleString()} cached</span>
            <span>{report.totals.errors} errors</span>
          </div>

          {report.by_kind && report.by_kind.length > 1 && <KindBreakdown byKind={report.by_kind} />}

          {report.by_origin && report.by_origin.some((o) => o.origin === "subagent" && o.calls > 0) && (
            <div className="mb-3.5 flex flex-wrap gap-4 text-sm">
              {report.by_origin.map((o) => (
                <span key={o.origin}>
                  <code>{o.origin === "subagent" ? "sub-agent" : "main chat"}</code>: $
                  {o.cost_usd.toFixed(4)} ({o.calls})
                </span>
              ))}
            </div>
          )}

          {groupBy === "day" && report.buckets.length > 1 && (
            <DayTrendChart buckets={report.buckets.map((b) => ({ label: b.bucket, cost: b.cost_usd }))} />
          )}

          <div className="overflow-x-auto">
          <table className="settings-table w-full border-collapse text-sm [&_td]:border-b [&_td]:border-border [&_td]:px-2.5 [&_td]:py-2 [&_td]:align-middle [&_th]:border-b [&_th]:border-border [&_th]:px-2.5 [&_th]:py-2 [&_th]:text-left [&_th]:font-semibold [&_th]:text-muted">
            <thead>
              <tr>
                <th>{groupBy}</th>
                <th>Cost</th>
                <th />
                <th>Calls</th>
                <th>Tokens in/out</th>
                <th>Cached</th>
                <th>Cache hits</th>
                <th>Errors</th>
              </tr>
            </thead>
            <tbody>
              {report.buckets.map((b) => (
                <tr key={b.bucket}>
                  <td className="nowrap whitespace-nowrap">{b.bucket}</td>
                  <td>${b.cost_usd.toFixed(4)}</td>
                  <td className="w-[120px]">
                    <span
                      className="block h-2.5 min-w-px rounded bg-accent"
                      style={{ width: `${(b.cost_usd / maxCost) * 100}%` }}
                    />
                  </td>
                  <td>{b.calls}</td>
                  <td className="text-xs text-muted">
                    {b.tokens_in.toLocaleString()} / {b.tokens_out.toLocaleString()}
                  </td>
                  <td>{b.cached_tokens.toLocaleString()}</td>
                  <td>{b.cache_hits}</td>
                  <td>{b.errors || ""}</td>
                </tr>
              ))}
              {report.buckets.length === 0 && (
                <tr>
                  <td colSpan={8} className="text-muted">
                    No usage in this range.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
          </div>
        </>
      )}
    </div>
  );
}

const SERIES_COLORS = ["var(--series-1)", "var(--series-2)", "var(--series-3)", "var(--series-4)"];

/** Cost per `kind` (chat/embedding/eval/…) as a labeled horizontal bar group —
 * replaces a bare inline "kind: $x (n)" text line with something you can
 * actually compare at a glance. Direct-labeled, so the legend is redundant
 * accessibility-wise, but still shown (>= 2 series convention, dataviz skill). */
function KindBreakdown({ byKind }: { byKind: { kind: string; cost_usd: number; calls: number }[] }) {
  const max = Math.max(1e-9, ...byKind.map((k) => k.cost_usd));
  return (
    <div className="mb-3.5 flex flex-col gap-1.5 rounded-lg border border-border p-3">
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-3xs text-muted">
        {byKind.map((k, i) => (
          <span key={k.kind} className="inline-flex items-center gap-1.5">
            <span
              className="inline-block size-2 rounded-full"
              style={{ background: SERIES_COLORS[i % SERIES_COLORS.length] }}
            />
            {k.kind}
          </span>
        ))}
      </div>
      {byKind.map((k, i) => (
        <div key={k.kind} className="flex items-center gap-2 text-xs">
          <span className="w-20 shrink-0 truncate text-muted">{k.kind}</span>
          <div className="h-4 flex-1 overflow-hidden rounded bg-bg-alt">
            <div
              className="h-full rounded"
              style={{
                width: `${(k.cost_usd / max) * 100}%`,
                background: SERIES_COLORS[i % SERIES_COLORS.length],
              }}
            />
          </div>
          <span className="w-24 shrink-0 text-right tabular-nums text-text">
            ${k.cost_usd.toFixed(4)}
          </span>
          <span className="w-16 shrink-0 text-right tabular-nums text-muted">{k.calls}×</span>
        </div>
      ))}
    </div>
  );
}

/** Day-by-day cost trend as an actual axis chart — the old version was a
 * single `<div>` tinted proportionally, with no scale, ticks, or way to read
 * an exact value short of the table below it. */
function DayTrendChart({ buckets }: { buckets: { label: string; cost: number }[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 640;
  const H = 180;
  const padL = 52;
  const padB = 24;
  const padT = 10;
  const padR = 10;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;

  const max = Math.max(...buckets.map((b) => b.cost), 1e-9);
  // Round the axis ceiling up to a "nice" step so ticks read as real numbers.
  const niceMax = useMemo(() => {
    const magnitude = Math.pow(10, Math.floor(Math.log10(max || 1)));
    const steps = [1, 2, 2.5, 5, 10];
    for (const s of steps) {
      if (max <= s * magnitude) return s * magnitude;
    }
    return 10 * magnitude;
  }, [max]);

  const barW = Math.min(28, (plotW / buckets.length) * 0.6);
  const gap = plotW / buckets.length;
  const yTicks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * niceMax);
  // Thin the x labels so they never collide, however many days are in range.
  const labelEvery = Math.max(1, Math.ceil(buckets.length / 8));

  return (
    <div className="mb-3.5 rounded-lg border border-border p-3">
      <div className="mb-1.5 text-2xs font-medium text-muted">Cost by day</div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        style={{ height: H }}
        role="img"
        aria-label={`Daily cost, ${buckets[0]?.label} through ${buckets[buckets.length - 1]?.label}, peak $${max.toFixed(4)}`}
      >
        {yTicks.map((t, i) => {
          const y = padT + plotH - (t / niceMax) * plotH;
          return (
            <g key={i}>
              <line
                x1={padL}
                x2={W - padR}
                y1={y}
                y2={y}
                stroke="var(--chart-grid)"
                strokeWidth={1}
              />
              <text x={padL - 8} y={y} textAnchor="end" dominantBaseline="middle" fontSize={10} fill="var(--muted)">
                ${t < 1 ? t.toFixed(3) : t.toFixed(2)}
              </text>
            </g>
          );
        })}
        <line
          x1={padL}
          x2={padL}
          y1={padT}
          y2={padT + plotH}
          stroke="var(--border)"
          strokeWidth={1}
        />
        <line
          x1={padL}
          x2={W - padR}
          y1={padT + plotH}
          y2={padT + plotH}
          stroke="var(--border)"
          strokeWidth={1}
        />
        {buckets.map((b, i) => {
          const cx = padL + gap * i + gap / 2;
          const h = niceMax > 0 ? (b.cost / niceMax) * plotH : 0;
          const y = padT + plotH - h;
          const isHover = hover === i;
          return (
            <g key={b.label}>
              <rect
                x={cx - barW / 2}
                y={y}
                width={barW}
                height={Math.max(h, b.cost > 0 ? 2 : 0)}
                rx={3}
                fill="var(--series-1)"
                opacity={isHover ? 1 : 0.85}
              />
              {/* full-height invisible hit target — the bar itself may be a
                  sliver for near-zero days, too small to hover reliably */}
              <rect
                x={cx - gap / 2}
                y={padT}
                width={gap}
                height={plotH}
                fill="transparent"
                onMouseEnter={() => setHover(i)}
                onMouseLeave={() => setHover((h) => (h === i ? null : h))}
              />
              {i % labelEvery === 0 && (
                <text
                  x={cx}
                  y={H - 6}
                  textAnchor="middle"
                  fontSize={9.5}
                  fill="var(--muted)"
                >
                  {b.label.slice(5)}
                </text>
              )}
            </g>
          );
        })}
        {hover != null && (
          <g>
            <line
              x1={padL + gap * hover + gap / 2}
              x2={padL + gap * hover + gap / 2}
              y1={padT}
              y2={padT + plotH}
              stroke="var(--muted)"
              strokeWidth={1}
              strokeDasharray="2 2"
            />
          </g>
        )}
      </svg>
      {hover != null && (
        <div className="text-2xs text-muted">
          <span className="font-medium text-text">{buckets[hover].label}</span> · $
          {buckets[hover].cost.toFixed(4)}
        </div>
      )}
    </div>
  );
}
