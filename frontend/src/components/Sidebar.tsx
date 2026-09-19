import { Clock, Moon, Search, Settings, Sun, X } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import type { ConversationSummary } from "../api/client";
import { useStore } from "../store/useStore";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";

const CRON_PREFIX = "[cron] ";

function dayBucket(iso: string): string {
  // A just-created conversation has no `updated_at` yet (optimistic insert in
  // Chat.tsx) — it belongs at the top of "Today", not in a bucket that isn't
  // in BUCKET_ORDER (which would silently drop it from the list).
  if (!iso) return "Today";
  const d = new Date(iso);
  const now = new Date();
  const startOf = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diffDays = Math.round((startOf(now) - startOf(d)) / 86_400_000);
  if (diffDays <= 0) return "Today";
  if (diffDays === 1) return "Yesterday";
  if (diffDays <= 7) return "Previous 7 days";
  return "Older";
}

const BUCKET_ORDER = ["Today", "Yesterday", "Previous 7 days", "Older"];

export default function Sidebar({
  conversations,
  currentId,
  badgeIds,
  onSelect,
  onNewChat,
  onDelete,
  onLogout,
  mobileOpen,
  onMobileClose,
}: {
  conversations: ConversationSummary[];
  currentId: string | null;
  /** Conversation ids with an unseen result (e.g. a cron run) — PLAN Phase 8. */
  badgeIds?: Set<string>;
  onSelect: (id: string) => void;
  onNewChat: () => void;
  onDelete: (id: string) => void;
  onLogout: () => void;
  /** Below the `lg` breakpoint the sidebar is an off-canvas drawer instead of
   * a static column — these control that; ignored at `lg` and up. */
  mobileOpen: boolean;
  onMobileClose: () => void;
}) {
  const { theme, toggleTheme, user } = useStore();
  const [query, setQuery] = useState("");

  const grouped = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = q
      ? conversations.filter((c) => (c.title || "New chat").toLowerCase().includes(q))
      : conversations;
    const buckets = new Map<string, ConversationSummary[]>();
    for (const c of filtered) {
      const key = dayBucket(c.updated_at);
      if (!buckets.has(key)) buckets.set(key, []);
      buckets.get(key)!.push(c);
    }
    return BUCKET_ORDER.map((key) => [key, buckets.get(key) ?? []] as const).filter(
      ([, items]) => items.length > 0,
    );
  }, [conversations, query]);

  return (
    <>
      {mobileOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/40 lg:hidden"
          onClick={onMobileClose}
          aria-hidden="true"
        />
      )}
      <aside
        className={cn(
          "sidebar fixed inset-y-0 left-0 z-40 flex w-[264px] -translate-x-full flex-col gap-2 border-r border-border bg-bg-alt p-3 transition-transform duration-200 ease-out",
          "lg:static lg:z-auto lg:translate-x-0 lg:transition-none",
          mobileOpen && "translate-x-0",
        )}
      >
        <div className="flex items-center gap-2 lg:hidden">
          <Button
            size="lg"
            className="flex-1"
            onClick={() => {
              onNewChat();
              onMobileClose();
            }}
          >
            + New chat
          </Button>
          <Button variant="ghost" size="icon" onClick={onMobileClose} aria-label="Close sidebar">
            <X className="size-4" />
          </Button>
        </div>
        <Button size="lg" className="hidden w-full lg:flex" onClick={onNewChat}>
          + New chat
        </Button>

        <label className="relative flex items-center">
          <Search className="pointer-events-none absolute left-2.5 size-3.5 text-faint" />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search conversations"
            aria-label="Search conversations"
            className="h-8 w-full rounded-lg border border-border bg-bg py-1 pl-8 pr-2 text-2xs text-text placeholder:text-faint focus-visible:outline-none focus-visible:border-accent"
          />
        </label>

      <nav className="conv-list flex flex-1 flex-col gap-2.5 overflow-y-auto">
        {grouped.map(([bucket, items]) => (
          <div key={bucket} className="flex flex-col gap-0.5">
            <div className="px-2.5 pb-0.5 pt-1 text-3xs font-medium uppercase tracking-wide text-muted">
              {bucket}
            </div>
            {items.map((c) => {
              const isCron = c.title?.startsWith(CRON_PREFIX) || c.title === "[cron]";
              const label = (isCron ? c.title!.replace(CRON_PREFIX, "").replace("[cron]", "") : c.title) || "New chat";
              const active = c.id === currentId;
              return (
                <div
                  key={c.id}
                  className={cn(
                    "conv-item group flex items-center gap-1 rounded-lg text-sm hover:bg-bg-elev",
                    active && "active border border-border bg-bg-elev",
                  )}
                >
                  <button
                    type="button"
                    aria-current={active ? "page" : undefined}
                    className="flex flex-1 items-center gap-2 overflow-hidden rounded-lg px-2.5 py-2 text-left"
                    onClick={() => {
                      onSelect(c.id);
                      onMobileClose();
                    }}
                  >
                    {isCron && (
                      <Clock className="size-3 shrink-0 text-orchestra" aria-label="Cron job run" />
                    )}
                    {badgeIds?.has(c.id) && (
                      <span
                        className="conv-badge size-1.5 shrink-0 rounded-full bg-accent"
                        title="New result"
                      />
                    )}
                    <span className="conv-title flex-1 overflow-hidden text-ellipsis whitespace-nowrap">
                      {label}
                    </span>
                    {c.origin === "overlay" && (
                      <span
                        className="conv-origin shrink-0 rounded bg-bg-alt px-1 text-3xs text-muted"
                        title="Chat from the desktop overlay"
                      >
                        Overlay
                      </span>
                    )}
                  </button>
                  <button
                    type="button"
                    aria-label={`Delete conversation "${label}"`}
                    className="conv-del shrink-0 border-none bg-transparent px-1 text-muted opacity-0 hover:text-danger focus-visible:opacity-100 group-hover:opacity-100 group-focus-within:opacity-100"
                    title="Delete"
                    onClick={() => onDelete(c.id)}
                  >
                    <X className="size-3.5" />
                  </button>
                </div>
              );
            })}
          </div>
        ))}
        {conversations.length === 0 && <div className="p-3 text-muted">No conversations yet</div>}
        {conversations.length > 0 && grouped.length === 0 && (
          <div className="p-3 text-muted">No conversations match "{query}"</div>
        )}
      </nav>
      <div className="sidebar-foot flex items-center gap-2 border-t border-border pt-2">
        <span className="flex-1 overflow-hidden text-ellipsis text-muted">{user?.username}</span>
        <Link
          to="/settings"
          className="foot-link px-1.5 py-1 text-muted hover:text-accent"
          title="Settings"
        >
          <Settings className="size-4" />
        </Link>
        <span className="flex items-center gap-1.5 text-muted">
          <Sun className="size-3.5" aria-hidden="true" />
          <Switch
            checked={theme === "dark"}
            onCheckedChange={() => toggleTheme()}
            aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
            title="Toggle theme"
          />
          <Moon className="size-3.5" aria-hidden="true" />
        </span>
        <Button variant="ghost" size="sm" onClick={onLogout}>
          Logout
        </Button>
      </div>
      </aside>
    </>
  );
}
