// Shared plumbing for Settings tabs — extracted in Phase 6 so Agents/Cron/MCP
// (which used to live inline in pages/Settings.tsx) and the other tabs stop
// each redefining the same error-wrapper/table chrome.
import { useState, type ReactNode } from "react";
import { cn } from "@/lib/utils";

export function useErr() {
  const [err, setErr] = useState<string | null>(null);
  const wrap = async (fn: () => Promise<void>) => {
    try {
      setErr(null);
      await fn();
    } catch (e) {
      setErr((e as Error)?.message || "Request failed");
    }
  };
  return { err, wrap, setErr };
}

export function SectionHead({ title, action }: { title: string; action?: ReactNode }) {
  return (
    <div className="settings-section-head mb-3 flex items-center justify-between">
      <h2 className="m-0 text-lg font-semibold">{title}</h2>
      {action}
    </div>
  );
}

export function SettingsForm({ children }: { children: ReactNode }) {
  return (
    <div className="settings-form mt-5 flex flex-col gap-3 rounded-md border border-border bg-bg-elev p-4 [&_label]:flex [&_label]:flex-col [&_label]:gap-1 [&_label]:text-2xs [&_label]:text-muted [&_label.checkbox]:flex-row [&_label.checkbox]:items-center [&_label.checkbox]:gap-1.5">
      {children}
    </div>
  );
}

export interface DataTableColumn<T> {
  header: string;
  className?: string;
  render: (row: T) => ReactNode;
}

/** Generic list table with empty/loading states — Agents/Cron/MCP each used to
 * hand-roll a <table> with no loading state at all (a slow fetch just looked
 * like an empty list). Deliberately no sort/filter: nothing in this app needs
 * either yet, and there's no third caller to justify building it speculatively. */
export function DataTable<T>({
  columns,
  rows,
  rowKey,
  loading,
  emptyMessage = "Nothing here yet.",
}: {
  columns: DataTableColumn<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  loading?: boolean;
  emptyMessage?: string;
}) {
  return (
    <table
      className={cn(
        "settings-table w-full border-collapse text-sm",
        "[&_td]:border-b [&_td]:border-border [&_td]:px-2.5 [&_td]:py-2 [&_td]:align-middle",
        "[&_th]:border-b [&_th]:border-border [&_th]:px-2.5 [&_th]:py-2 [&_th]:text-left [&_th]:font-semibold [&_th]:text-muted",
        // Below `md` there's no room for columns — each row becomes a
        // labeled card instead of a table that either overflows or gets
        // unreadably squeezed. Same markup both ways: only display/layout
        // changes, so a screen reader still gets a real <table>.
        "max-md:block",
        "[&_thead]:max-md:hidden",
        "[&_tbody]:max-md:block",
        "[&_tr]:max-md:mb-2 [&_tr]:max-md:block [&_tr]:max-md:rounded-lg [&_tr]:max-md:border [&_tr]:max-md:border-border [&_tr]:max-md:p-1",
        "[&_td]:max-md:flex [&_td]:max-md:items-center [&_td]:max-md:justify-between [&_td]:max-md:gap-3 [&_td]:max-md:border-b-0 [&_td]:max-md:px-1.5 [&_td]:max-md:py-1",
        "[&_td]:max-md:before:mr-2 [&_td]:max-md:before:shrink-0 [&_td]:max-md:before:text-xs [&_td]:max-md:before:font-semibold [&_td]:max-md:before:text-muted [&_td]:max-md:before:content-[attr(data-label)]",
        "[&_td:empty]:max-md:hidden",
      )}
    >
      <thead>
        <tr>
          {columns.map((c) => (
            <th key={c.header} className={c.className}>
              {c.header}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {loading ? (
          <tr>
            <td colSpan={columns.length} className="py-6 text-center text-muted">
              Loading…
            </td>
          </tr>
        ) : rows.length === 0 ? (
          <tr>
            <td colSpan={columns.length} className="py-6 text-center text-muted">
              {emptyMessage}
            </td>
          </tr>
        ) : (
          rows.map((row) => (
            <tr key={rowKey(row)}>
              {columns.map((c) => (
                <td key={c.header} data-label={c.header} className={c.className}>
                  {c.render(row)}
                </td>
              ))}
            </tr>
          ))
        )}
      </tbody>
    </table>
  );
}
