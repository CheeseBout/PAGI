import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Link } from "react-router-dom";
import AgentEvalTab from "../components/settings/AgentEvalTab";
import AgentsTab from "../components/settings/AgentsTab";
import CronTab from "../components/settings/CronTab";
import EvalTab from "../components/settings/EvalTab";
import KnowledgeTab from "../components/settings/KnowledgeTab";
import McpTab from "../components/settings/McpTab";
import PlaygroundTab from "../components/settings/PlaygroundTab";
import ProjectsTab from "../components/settings/ProjectsTab";
import UsageTab from "../components/settings/UsageTab";
import { cn } from "@/lib/utils";

type TabKey =
  | "agents"
  | "knowledge"
  | "playground"
  | "eval"
  | "agent-eval"
  | "cron"
  | "mcp"
  | "projects"
  | "usage";

const TAB_LABEL: Record<TabKey, string> = {
  agents: "Agents",
  knowledge: "Knowledge",
  playground: "Playground",
  eval: "RAG eval",
  "agent-eval": "Agent eval",
  cron: "Cron jobs",
  mcp: "MCP servers",
  projects: "Projects",
  usage: "Usage",
};

// Three groups by *intent*, not by feature area — the old flat 8-tab row mixed
// "configure a thing", "watch what happened", and "try an idea before
// committing to it" as if they were the same kind of task (SPEC audit finding).
// "Automation" (Phase 18) is its own group — a project run is neither a
// static config nor a one-off experiment, it's a standing background job.
const GROUPS: { label: string; tabs: TabKey[] }[] = [
  { label: "Configuration", tabs: ["agents", "knowledge", "mcp", "cron"] },
  { label: "Automation", tabs: ["projects"] },
  { label: "Observability", tabs: ["usage"] },
  { label: "Experimentation", tabs: ["playground", "eval", "agent-eval"] },
];
const TAB_KEYS: TabKey[] = GROUPS.flatMap((g) => g.tabs);

/** Each tab is its own route (`/settings/:tab`) so it's deep-linkable and
 * survives a reload — it used to be a bare `useState`, so an F5 always
 * dropped you back on "Agents". */
export default function Settings() {
  const { tab: tabParam } = useParams<{ tab: string }>();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const tab: TabKey = TAB_KEYS.includes(tabParam as TabKey) ? (tabParam as TabKey) : "agents";

  return (
    <div className="settings-page mx-auto max-w-[1400px] px-5 pb-16 pt-6">
      <header className="settings-header">
        <Link to="/" className="settings-back text-sm text-muted no-underline">
          ← Back to chat
        </Link>
        <h1 className="my-2 text-2xl font-semibold">Settings</h1>
      </header>

      <div className="mt-5 flex flex-col gap-6 md:flex-row md:items-start md:gap-8">
        <nav className="settings-tabs flex shrink-0 flex-col gap-4 md:w-48">
          {GROUPS.map((group) => (
            <div key={group.label} className="flex flex-col gap-0.5">
              <div className="px-2.5 pb-1 text-3xs font-semibold uppercase tracking-wide text-muted">
                {group.label}
              </div>
              {group.tabs.map((k) => (
                <button
                  key={k}
                  aria-current={tab === k ? "page" : undefined}
                  className={cn(
                    "rounded-lg px-2.5 py-1.5 text-left text-sm text-text hover:bg-bg-alt",
                    tab === k && "active bg-bg-alt font-medium text-accent",
                  )}
                  onClick={() => navigate(`/settings/${k}`)}
                >
                  {TAB_LABEL[k]}
                </button>
              ))}
            </div>
          ))}
        </nav>

        <div className="settings-body min-w-0 flex-1">
          {tab === "agents" && <AgentsTab />}
          {tab === "knowledge" && <KnowledgeTab />}
          {tab === "playground" && <PlaygroundTab initialQueryLogId={searchParams.get("log")} />}
          {tab === "eval" && <EvalTab />}
          {tab === "agent-eval" && <AgentEvalTab />}
          {tab === "cron" && <CronTab />}
          {tab === "mcp" && <McpTab />}
          {tab === "projects" && <ProjectsTab />}
          {tab === "usage" && <UsageTab />}
        </div>
      </div>
    </div>
  );
}
