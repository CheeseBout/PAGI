// Single right-side panel for a conversation, tabbed (Files | Trace |
// Citations) instead of Files-as-sidebar + Trace-pushing-the-chat-down-below
// (the old layout — opening Trace ate vertical space instead of sharing the
// same panel real estate as Files).
import { X } from "lucide-react";
import type { ChatMessage } from "../api/client";
import CitationList from "./CitationList";
import { PatternTrace } from "./PatternTrace";
import SubAgentTreeView from "./SubAgentTreeView";
import WorkspacePanel from "./WorkspacePanel";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";

export type RightPanelTab = "files" | "trace" | "citations";

export default function RightPanel({
  sessionId,
  tab,
  onTabChange,
  onClose,
  workspaceRefreshKey,
  messages,
}: {
  sessionId: string;
  tab: RightPanelTab;
  onTabChange: (tab: RightPanelTab) => void;
  onClose: () => void;
  workspaceRefreshKey: number;
  messages: ChatMessage[];
}) {
  const citedMessages = messages.filter(
    (m) => m.role === "assistant" && m.rag && (m.rag.citations.length > 0 || m.rag.no_context),
  );

  return (
    <aside
      className={cn(
        "right-panel flex w-80 shrink-0 flex-col overflow-hidden border-l border-border bg-bg-alt",
        // Below `xl` there isn't room to share the row with the chat — float
        // it over everything instead of squeezing ChatWindow down further.
        "fixed inset-y-0 right-0 z-40 max-w-[85vw] xl:static xl:z-auto xl:max-w-none",
      )}
    >
      <Tabs
        value={tab}
        onValueChange={(v) => onTabChange(v as RightPanelTab)}
        className="flex min-h-0 flex-1 flex-col"
      >
        <div className="flex items-center justify-between gap-2 border-b border-border px-2">
          <TabsList className="border-none">
            <TabsTrigger value="files">Files</TabsTrigger>
            <TabsTrigger value="trace">Trace</TabsTrigger>
            <TabsTrigger value="citations">Citations</TabsTrigger>
          </TabsList>
          <Button variant="ghost" size="icon" className="size-7 shrink-0" onClick={onClose} title="Close panel">
            <X className="size-3.5" />
          </Button>
        </div>

        <TabsContent value="files" className="mt-0 min-h-0 flex-1 overflow-y-auto">
          <WorkspacePanel sessionId={sessionId} refreshKey={workspaceRefreshKey} />
        </TabsContent>

        <TabsContent value="trace" className="mt-0 min-h-0 flex-1 overflow-y-auto p-2.5">
          <div className="mb-3">
            <SubAgentTreeView conversationId={sessionId} />
          </div>
          <PatternTrace conversationId={sessionId} />
        </TabsContent>

        <TabsContent value="citations" className="mt-0 min-h-0 flex-1 overflow-y-auto p-2.5">
          {citedMessages.length === 0 ? (
            <div className="text-xs text-muted">No RAG retrieval in this conversation yet.</div>
          ) : (
            <div className="flex flex-col gap-3">
              {citedMessages.map((m) => (
                <div key={m.id} className="border-b border-border pb-3 last:border-0 last:pb-0">
                  <CitationList rag={m.rag!} />
                </div>
              ))}
            </div>
          )}
        </TabsContent>
      </Tabs>
    </aside>
  );
}
