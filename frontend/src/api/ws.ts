// WebSocket transport for one conversation (SPEC §3).

export type ServerEvent =
  | { type: "token"; message_id: string; content: string }
  | { type: "tool_call_start"; tool_call_id: string; tool_name: string; args: Record<string, unknown> }
  | { type: "tool_call_result"; tool_call_id: string; result: unknown }
  | {
      type: "approval_required";
      approval_id: string;
      tool_call_id: string;
      tool_name: string;
      args: Record<string, unknown>;
    }
  | { type: "approval_resolved"; approval_id: string; status: string }
  | {
      type: "message_done";
      message_id: string;
      tokens_in: number;
      tokens_out: number;
      cached_tokens?: number;
    }
  // RAG (Phase 12) — citation chips render from message.rag after reload;
  // this live event drives the "retrieved N passages" line while streaming.
  | {
      type: "rag_retrieval";
      query_log_id: string | null;
      chunk_count: number;
      total_ms: number;
      citations: {
        chunk_id: string;
        document_id: string;
        title: string;
        source_uri: string;
        score: number;
        updated_at: string | null;
      }[];
    }
  | { type: "rag_no_context"; query_log_id: string | null; reason: string }
  // ── sub-agent (Phase 13, SPEC §3.2 / §15.4) ──────────────────────
  | {
      type: "subagent";
      sub_session_id: string;
      parent_session_id?: string;
      agent_id?: string;
      agent_name: string;
      depth: number;
      event: ServerEvent;
    }
  | {
      type: "subagent_started";
      sub_session_id: string;
      agent_name: string;
      task: string;
      depth: number;
    }
  | {
      type: "subagent_done";
      sub_session_id: string;
      status: string;
      tokens_in: number;
      tokens_out: number;
      cost_usd: number;
    }
  | { type: "subagent_limit"; limit: string; detail: string; sub_session_id: string | null }
  // ── orchestration patterns (Phase 14, SPEC §3.2) ────────────────
  | { type: "plan_created"; run_id?: string; steps: Record<string, unknown>[] }
  | { type: "step_started"; run_id?: string; step: number; goal: string }
  | { type: "step_done"; run_id?: string; step: number; status: string }
  | {
      type: "reflection";
      run_id?: string;
      attempt: number;
      score?: number;
      reason?: string;
      lesson?: string;
    }
  | {
      type: "route_decision";
      run_id?: string;
      route: string | null;
      confidence: number;
      reason: string;
    }
  | { type: "worker_started"; run_id?: string; agent_name: string; task: string }
  | { type: "worker_result"; run_id?: string; agent_name: string; status: string }
  | { type: "debate_vote"; run_id?: string; winner: string; rationale: string }
  | { type: "ab_arm"; pattern: string; challenger: boolean }
  | { type: "error"; code: string; message: string };

const WS_BASE = import.meta.env.VITE_WS_BASE_URL || "/ws";

function wsUrl(sessionId: string): string {
  if (WS_BASE.startsWith("ws://") || WS_BASE.startsWith("wss://")) {
    return `${WS_BASE}/chat/${sessionId}`;
  }
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}${WS_BASE}/chat/${sessionId}`;
}

export class ChatSocket {
  private ws: WebSocket | null = null;
  private closedByUs = false;

  constructor(
    private sessionId: string,
    private onEvent: (e: ServerEvent) => void,
    private onStatus: (s: "open" | "closed") => void,
  ) {}

  connect() {
    this.closedByUs = false;
    const ws = new WebSocket(wsUrl(this.sessionId));
    this.ws = ws;
    ws.onopen = () => this.onStatus("open");
    ws.onclose = () => {
      this.onStatus("closed");
      if (!this.closedByUs) setTimeout(() => this.connect(), 1500);
    };
    ws.onmessage = (ev) => {
      try {
        this.onEvent(JSON.parse(ev.data) as ServerEvent);
      } catch {
        /* ignore malformed frame */
      }
    };
  }

  private send(payload: unknown) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(payload));
  }

  sendUserMessage(content: string, attachmentIds: string[] = []) {
    this.send({ type: "user_message", content, attachment_ids: attachmentIds });
  }
  abort() {
    this.send({ type: "abort" });
  }
  regenerate() {
    this.send({ type: "regenerate" });
  }
  editMessage(messageId: string, content: string) {
    this.send({ type: "edit_message", message_id: messageId, content });
  }
  decideApproval(approvalId: string, decision: "approve" | "deny", remember?: "session") {
    this.send({ type: "approval_decision", approval_id: approvalId, decision, remember });
  }
  close() {
    this.closedByUs = true;
    this.ws?.close();
  }
}
