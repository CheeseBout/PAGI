import { Compass, Folder, Menu, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  api,
  type Agent,
  type ChatMessage,
  type ConversationSummary,
  type Grant,
} from "../api/client";
import { useChatSession } from "../features/chat/useChatSession";
import AvatarCanvas, { type AvatarConfig } from "../components/Avatar/AvatarCanvas";
import ChatWindow, { type ToolEvent } from "../components/ChatWindow";
import InputBox from "../components/InputBox";
import ModelPicker from "../components/ModelPicker";
import RightPanel, { type RightPanelTab } from "../components/RightPanel";
import Sidebar from "../components/Sidebar";
import { useAvatarState } from "../hooks/useAvatarState";
import { useStore } from "../store/useStore";
import { Button } from "@/components/ui/button";
import { ErrorBanner } from "@/components/ui/error-banner";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 50;
const CRON_SEEN_KEY = "pagi-cron-seen";

function loadCronSeen(): Record<string, string> {
  try {
    return JSON.parse(localStorage.getItem(CRON_SEEN_KEY) || "{}");
  } catch {
    return {};
  }
}

export default function Chat() {
  const navigate = useNavigate();
  const setUser = useStore((s) => s.setUser);

  const [agents, setAgents] = useState<Agent[]>([]);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [newAgentId, setNewAgentId] = useState<string>("");
  const [cronSeen, setCronSeen] = useState<Record<string, string>>(loadCronSeen);

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [messagesLoadError, setMessagesLoadError] = useState<string | null>(null);
  const [grants, setGrants] = useState<Grant[]>([]);
  const [rightPanel, setRightPanel] = useState<RightPanelTab | null>(null);
  const [wsRefreshKey, setWsRefreshKey] = useState(0);
  const [sidebarOpen, setSidebarOpen] = useState(false);

  function toggleRightPanel(target: RightPanelTab) {
    setRightPanel((cur) => (cur === target ? null : target));
  }

  const refreshConversations = useCallback(() => {
    api.listConversations().then(setConversations).catch(() => {});
  }, []);

  const reload = useCallback((id: string) => {
    setMessagesLoadError(null);
    setMessagesLoading(true);
    return api
      .getConversation(id)
      .then((data) => {
        setMessages(data.messages);
        setHasMore(data.messages.length >= PAGE_SIZE);
      })
      .catch(() => setMessagesLoadError("Couldn't load this conversation."))
      .finally(() => setMessagesLoading(false));
  }, []);

  // A turn finished. Appending it locally (keyed on the server's own message
  // id) avoids the visible gap/flash of waiting for `reload` before the
  // streamed bubble turns into a committed message. Only safe when the turn
  // used no tools — a tool-using turn is actually several DB rows (assistant
  // w/ tool_calls, tool-result rows, final assistant) and reconstructing that
  // shape client-side risks getting it wrong, so that path just waits on
  // `reload` like before. Either way `reload` still runs, to pick up fields
  // only the server knows (RAG citations, the canonical timestamp).
  const chat = useChatSession(currentId, {
    onMessageDone: ({ messageId, content, hadToolCalls, tokensIn, tokensOut }) => {
      if (!currentId) return;
      if (!hadToolCalls && content) {
        setMessages((prev) =>
          prev.some((m) => m.id === messageId)
            ? prev
            : [
                ...prev,
                {
                  id: messageId,
                  role: "assistant",
                  content,
                  tokens_in: tokensIn,
                  tokens_out: tokensOut,
                  created_at: new Date().toISOString(),
                },
              ],
        );
      }
      reload(currentId);
      refreshConversations();
    },
    onApprovalResolved: () => {
      if (currentId) api.listGrants(currentId).then(setGrants).catch(() => {});
    },
    onFileToolTouched: () => setWsRefreshKey((k) => k + 1),
  });

  // -- initial load ---------------------------------------------------------
  useEffect(() => {
    api
      .listAgents()
      .then((a) => {
        setAgents(a);
        setNewAgentId(a.find((x) => x.is_default)?.id ?? a[0]?.id ?? "");
      })
      .catch(() => {});
    refreshConversations();
    // deep link from the desktop overlay's "open full history" (SPEC §21.4.3)
    const wanted = new URLSearchParams(window.location.search).get("session");
    if (wanted) setCurrentId(wanted);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // -- reset + fetch page-owned state whenever the open conversation changes.
  // The live turn state (streaming/tools/approvals/socket) resets itself —
  // see useChatSession's own currentId-effect.
  useEffect(() => {
    setMessages([]);
    setHasMore(false);
    setMessagesLoadError(null);
    setGrants([]);
    setRightPanel(null);
    if (!currentId) return;

    reload(currentId);
    api
      .listPendingApprovals()
      .then((all) => chat.seedApprovals(all.filter((a) => a.session_id === currentId)))
      .catch(() => {});
    api.listGrants(currentId).then(setGrants).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentId]);

  // -- cron "new result" badge (PLAN Phase 8) — tracked client-side --------
  function markCronSeen(c: ConversationSummary) {
    if (!c.title?.startsWith("[cron]")) return;
    setCronSeen((prev) => {
      if (prev[c.id] === c.updated_at) return prev;
      const next = { ...prev, [c.id]: c.updated_at };
      try {
        localStorage.setItem(CRON_SEEN_KEY, JSON.stringify(next));
      } catch {
        /* storage unavailable — badge just won't persist across reloads */
      }
      return next;
    });
  }
  const cronBadgeIds = new Set(
    conversations
      .filter((c) => c.title?.startsWith("[cron]") && cronSeen[c.id] !== c.updated_at)
      .map((c) => c.id),
  );

  function selectConversation(id: string) {
    setCurrentId(id);
    const c = conversations.find((x) => x.id === id);
    if (c) markCronSeen(c);
  }

  // -- actions ---------------------------------------------------------------
  async function newChat() {
    if (!newAgentId) return;
    const { id } = await api.createConversation(newAgentId);
    setConversations((prev) => [{ id, title: null, agent_id: newAgentId, updated_at: "" }, ...prev]);
    setCurrentId(id);
  }

  async function deleteConversation(id: string) {
    await api.deleteConversation(id);
    setConversations((prev) => prev.filter((c) => c.id !== id));
    if (currentId === id) setCurrentId(null);
  }

  function sendMessage(text: string, attachmentIds: string[] = []) {
    const optimistic: ChatMessage = {
      id: `local-${Date.now()}`,
      role: "user",
      content: text,
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, optimistic]);
    chat.sendUserMessage(text, attachmentIds);
  }

  async function revokeGrant(tool: string) {
    if (!currentId) return;
    await api.revokeGrant(currentId, tool).catch(() => {});
    api.listGrants(currentId).then(setGrants).catch(() => {});
  }

  const loadOlder = useCallback(async () => {
    if (!currentId || messages.length === 0) return;
    const oldest = messages[0];
    const data = await api.getConversation(currentId, oldest.id);
    setMessages((prev) => [...data.messages, ...prev]);
    setHasMore(data.messages.length >= PAGE_SIZE);
  }, [currentId, messages]);

  const editMessage = useCallback(
    (messageId: string, content: string) => {
      setMessages((prev) => {
        const idx = prev.findIndex((m) => m.id === messageId);
        if (idx === -1) return prev;
        const truncated = prev.slice(0, idx + 1);
        truncated[idx] = { ...truncated[idx], content };
        return truncated;
      });
      chat.editMessage(messageId, content);
    },
    [chat.editMessage],
  );

  const regenerate = useCallback(() => {
    setMessages((prev) => {
      const idx = [...prev].reverse().findIndex((m) => m.role === "user");
      if (idx === -1) return prev;
      return prev.slice(0, prev.length - idx);
    });
    chat.regenerate();
  }, [chat.regenerate]);

  async function logout() {
    await api.logout();
    setUser(null);
    navigate("/login");
  }

  const currentAgent = agents.find(
    (a) => a.id === conversations.find((c) => c.id === currentId)?.agent_id,
  );
  const { live } = chat;
  const avatarState = useAvatarState(chat.latestEvent, chat.sendPulse);
  const avatarConfig = currentAgent?.avatar_config as AvatarConfig | undefined;

  return (
    <div className="app-shell flex h-dvh">
      <Sidebar
        conversations={conversations}
        currentId={currentId}
        badgeIds={cronBadgeIds}
        onSelect={selectConversation}
        onNewChat={newChat}
        onDelete={deleteConversation}
        onLogout={logout}
        mobileOpen={sidebarOpen}
        onMobileClose={() => setSidebarOpen(false)}
      />
      <main className="main-pane flex min-w-0 flex-1 flex-col">
        <header className="topbar flex flex-wrap items-center gap-2.5 border-b border-border px-4 py-2.5 text-sm">
          <Button
            variant="ghost"
            size="icon"
            className="lg:hidden"
            onClick={() => setSidebarOpen(true)}
            aria-label="Open sidebar"
          >
            <Menu className="size-4" />
          </Button>
          {currentId ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="topbar-agent min-w-0 truncate font-semibold">
                  {currentAgent ? currentAgent.name : "…"}
                </span>
              </TooltipTrigger>
              {currentAgent && (
                <TooltipContent className="font-mono">
                  {currentAgent.provider}/{currentAgent.model}
                </TooltipContent>
              )}
            </Tooltip>
          ) : (
            <>
              <span className="text-muted">New chat agent:</span>
              <ModelPicker agents={agents} value={newAgentId} onChange={setNewAgentId} />
            </>
          )}
          {currentId && (
            <Button
              variant="outline"
              size="sm"
              className={cn(
                "workspace-toggle px-2.5 py-1",
                rightPanel === "files" && "active border-accent text-accent",
              )}
              onClick={() => toggleRightPanel("files")}
              title="Toggle workspace file browser"
            >
              <Folder className="size-3.5" /> Files
            </Button>
          )}
          {currentId && (
            <Button
              variant="outline"
              size="sm"
              className={cn(
                "workspace-toggle px-2.5 py-1",
                rightPanel === "trace" && "active border-accent text-accent",
              )}
              onClick={() => toggleRightPanel("trace")}
              title="Show the orchestration pattern trace and sub-agent tree for this conversation"
            >
              <Compass className="size-3.5" /> Trace
            </Button>
          )}
          <span
            role="status"
            className="ml-auto flex items-center gap-1.5 text-2xs text-muted"
            title={chat.wsOpen ? "connected" : "disconnected"}
          >
            <span
              aria-hidden="true"
              className={cn("ws-dot size-2.5 rounded-full", chat.wsOpen ? "on bg-success" : "off bg-danger")}
            />
            {chat.wsOpen ? "Connected" : "Disconnected"}
          </span>
        </header>

        {grants.length > 0 && (
          <div className="grants-strip flex flex-wrap items-center gap-2 border-b border-border bg-bg-alt px-4 py-1.5">
            <span className="text-xs text-muted">Always allowed this chat:</span>
            {grants.map((g) => (
              <span
                key={g.id}
                className="grant-chip inline-flex items-center gap-1 rounded-full border border-border py-0.5 pl-2 pr-1 text-2xs"
              >
                <code>{g.tool_name}</code>
                <button
                  onClick={() => revokeGrant(g.tool_name)}
                  title="Revoke"
                  className="border-none bg-transparent px-1 py-0 leading-none text-muted hover:text-danger"
                >
                  <X className="size-3" />
                </button>
              </span>
            ))}
          </div>
        )}

        <ErrorBanner message={live.error} inline />

        {currentId ? (
          <>
            <div className="chat-and-workspace flex min-h-0 flex-1">
              <ChatWindow
                key={currentId}
                messages={messages}
                loading={messagesLoading}
                loadError={messagesLoadError}
                onRetryLoad={() => reload(currentId)}
                streamingText={live.streamingText}
                streamingRag={live.streamingRag}
                toolEvents={live.toolEvents as ToolEvent[]}
                subAgents={Object.values(live.subAgents)}
                patternLines={live.patternLines}
                approvals={live.approvals}
                busy={live.streamingText !== null}
                hasMore={hasMore}
                onDecision={chat.decideApproval}
                onLoadOlder={loadOlder}
                onEditMessage={editMessage}
                onRegenerate={regenerate}
              />
              {avatarConfig?.enabled && currentAgent && (
                <div className="avatar-column hidden w-64 shrink-0 border-l border-border sm:block">
                  <AvatarCanvas
                    agentId={currentAgent.id}
                    avatarConfig={avatarConfig}
                    aiState={avatarState}
                    className="h-full w-full"
                  />
                </div>
              )}
              {rightPanel && (
                <>
                  <div
                    className="fixed inset-0 z-30 bg-black/40 xl:hidden"
                    onClick={() => setRightPanel(null)}
                    aria-hidden="true"
                  />
                  <RightPanel
                    sessionId={currentId}
                    tab={rightPanel}
                    onTabChange={setRightPanel}
                    onClose={() => setRightPanel(null)}
                    workspaceRefreshKey={wsRefreshKey}
                    messages={messages}
                  />
                </>
              )}
            </div>
            <InputBox
              onSend={sendMessage}
              onAbort={chat.abort}
              streaming={live.streamingText !== null}
              disabled={!chat.wsOpen}
              sessionId={currentId}
              onError={chat.reportError}
            />
          </>
        ) : (
          <div className="center-screen flex min-h-dvh flex-col items-center justify-center gap-4 p-4">
            <Button size="lg" onClick={newChat}>
              + Start a new chat
            </Button>
          </div>
        )}
      </main>
    </div>
  );
}
