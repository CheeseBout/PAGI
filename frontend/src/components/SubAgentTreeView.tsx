// Persisted sub-agent delegation tree (Phase 13, SPEC §15) — survives reload,
// unlike the live SubAgentTrace cards (which only exist during/right after a
// streaming turn and have no parent-child linkage to build a real tree from).
// `api.conversationTree` already returns exactly that linkage
// (`parent_session_id`) but nothing in the UI called it before this.
import { CheckCircle2, CircleDot, XCircle } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { api, type SubAgentNode } from "../api/client";

const STATUS_ICON: Record<string, ReactNode> = {
  ok: <CheckCircle2 className="size-3.5 shrink-0 text-success" />,
  denied: <XCircle className="size-3.5 shrink-0 text-danger" />,
  running: <CircleDot className="size-3.5 shrink-0 animate-pulse text-orchestra" />,
};

function TreeNode({ node, children }: { node: SubAgentNode; children: ReactNode }) {
  return (
    <li>
      <div className="flex items-center gap-1.5 text-xs">
        {STATUS_ICON[node.status] ?? <span className="text-muted">·</span>}
        <b>{node.agent_name}</b>
        {node.delegated_task && (
          <span className="truncate text-muted"> — {node.delegated_task}</span>
        )}
        {node.tokens > 0 && <span className="shrink-0 text-muted"> · {node.tokens} tok</span>}
        {node.cost_usd > 0 && <span className="shrink-0 text-muted"> · ${node.cost_usd.toFixed(4)}</span>}
      </div>
      {children}
    </li>
  );
}

export default function SubAgentTreeView({ conversationId }: { conversationId: string }) {
  const [data, setData] = useState<{ root: string; nodes: SubAgentNode[] } | null>(null);

  useEffect(() => {
    setData(null);
    api
      .conversationTree(conversationId)
      .then(setData)
      .catch(() => setData({ root: conversationId, nodes: [] }));
  }, [conversationId]);

  if (data === null) return <div className="text-xs text-muted">loading sub-agents…</div>;
  if (data.nodes.length === 0) return null; // no delegation happened in this conversation

  const byParent = new Map<string, SubAgentNode[]>();
  for (const n of data.nodes) {
    const key = n.parent_session_id ?? data.root;
    byParent.set(key, [...(byParent.get(key) ?? []), n]);
  }

  function renderLevel(parentId: string): ReactNode {
    const children = byParent.get(parentId);
    if (!children || children.length === 0) return null;
    return (
      <ul className="flex flex-col gap-1.5 border-l border-border pl-3">
        {children.map((n) => (
          <TreeNode key={n.session_id} node={n}>
            {renderLevel(n.session_id)}
          </TreeNode>
        ))}
      </ul>
    );
  }

  return (
    <div>
      <div className="mb-1.5 text-xs font-semibold text-muted">Sub-agents</div>
      {renderLevel(data.root)}
    </div>
  );
}
