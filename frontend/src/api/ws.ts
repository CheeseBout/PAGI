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

// Reconnect backoff (SPEC §21.10): 1.5s, doubling up to a 30s ceiling; reset once
// a connection opens. The web UI benefits too — a down backend used to be
// retried every 1.5s forever.
const RECONNECT_BASE_MS = 1500;
const RECONNECT_MAX_MS = 30_000;

export function reconnectDelay(attempt: number): number {
  return Math.min(RECONNECT_MAX_MS, RECONNECT_BASE_MS * 2 ** Math.max(0, attempt));
}

/** Close code the server uses for "not logged in" (SPEC §3). */
export const WS_CLOSE_UNAUTHENTICATED = 4401;

export class ChatSocket {
  private ws: WebSocket | null = null;
  private closedByUs = false;
  private attempt = 0;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(
    private sessionId: string,
    private onEvent: (e: ServerEvent) => void,
    private onStatus: (s: "open" | "closed") => void,
    /** server closed with 4401 — reconnecting can't help until the user logs in again */
    private onAuthLost?: () => void,
  ) {}

  connect() {
    this.closedByUs = false;
    const ws = new WebSocket(wsUrl(this.sessionId));
    this.ws = ws;
    ws.onopen = () => {
      this.attempt = 0;
      this.onStatus("open");
    };
    ws.onclose = (ev) => {
      this.onStatus("closed");
      if (this.closedByUs) return;
      if (ev.code === WS_CLOSE_UNAUTHENTICATED) {
        this.closedByUs = true;
        this.onAuthLost?.();
        return;
      }
      this.retryTimer = setTimeout(() => this.connect(), reconnectDelay(this.attempt++));
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
    if (this.retryTimer) clearTimeout(this.retryTimer);
    this.ws?.close();
  }
}

// ── global notification stream (SPEC §21.7) ─────────────────────────────
export type NotificationEvent =
  | { type: "hello"; pending_approvals: number }
  | { type: "ping" }
  | {
      type: "cron_run_finished";
      job_id: string;
      job_name: string;
      session_id: string;
      status: "ok" | "error";
      summary: string;
    }
  | {
      type: "project_iteration_finished";
      project_run_id: string;
      project_name: string;
      iteration_no: number;
      qa_verdict: string | null;
      status: "done" | "qa_failed" | "error";
    }
  | { type: "project_paused"; project_run_id: string; project_name: string; reason: string }
  | {
      type: "approval_pending";
      approval_id: string;
      session_id: string;
      tool_name: string;
      args_preview: string;
    }
  | { type: "approval_resolved"; approval_id: string; status: string };

function notificationsUrl(): string {
  if (WS_BASE.startsWith("ws://") || WS_BASE.startsWith("wss://")) return `${WS_BASE}/notifications`;
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}${WS_BASE}/notifications`;
}

/** One-way server -> client stream, reconnecting with the same backoff as
 * ChatSocket. Delivery is at-most-once with no replay, so `onOpen` fires on
 * every (re)connect: the owner must re-sync from REST there (SPEC §21.7). */
export class NotificationSocket {
  private ws: WebSocket | null = null;
  private closedByUs = false;
  private attempt = 0;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(
    private onEvent: (e: NotificationEvent) => void,
    private onOpen?: () => void,
    private onAuthLost?: () => void,
  ) {}

  connect() {
    this.closedByUs = false;
    const ws = new WebSocket(notificationsUrl());
    this.ws = ws;
    ws.onopen = () => {
      this.attempt = 0;
      this.onOpen?.();
    };
    ws.onclose = (ev) => {
      if (this.closedByUs) return;
      if (ev.code === WS_CLOSE_UNAUTHENTICATED) {
        this.closedByUs = true;
        this.onAuthLost?.();
        return;
      }
      this.retryTimer = setTimeout(() => this.connect(), reconnectDelay(this.attempt++));
    };
    ws.onmessage = (ev) => {
      try {
        this.onEvent(JSON.parse(ev.data) as NotificationEvent);
      } catch {
        /* ignore malformed frame */
      }
    };
  }

  close() {
    this.closedByUs = true;
    if (this.retryTimer) clearTimeout(this.retryTimer);
    this.ws?.close();
    this.ws = null;
  }
}
