// Pure reducer for the ephemeral, WebSocket-driven part of a chat turn.
//
// Deliberately excludes `messages` (owned by the page — pagination/edit/
// regenerate touch it too) and I/O side effects (refetching the conversation,
// refreshing grants). Those are triggered by `useChatSession` reacting to the
// fields this reducer surfaces (`lastMessageDone`, etc.), not from inside here.
import type { Approval } from "@/api/client";
import type { ServerEvent } from "@/api/ws";
import type { ToolEvent } from "@/components/ChatWindow";
import type { SubAgentEntry } from "@/components/SubAgentTrace";

export interface StreamingRag {
  count: number;
  ms: number;
  no_context?: boolean;
  reason?: string;
}

export interface MessageDoneInfo {
  messageId: string;
  content: string;
  hadToolCalls: boolean;
  /** From the WS event itself — available immediately, before `reload` confirms
   * it against the server. `cost_usd`/`latency_ms` aren't on this event (only
   * tokens are), so those only show up once `reload` brings back the message
   * enriched from its `Trace` row. */
  tokensIn: number;
  tokensOut: number;
}

export interface ChatLiveState {
  streamingText: string | null;
  streamingRag: StreamingRag | null;
  toolEvents: ToolEvent[];
  subAgents: Record<string, SubAgentEntry>;
  patternLines: string[];
  approvals: Approval[];
  error: string | null;
  /** Set once per completed turn; `useChatSession` watches this to trigger the
   * page's message-list commit + conversation-list refresh side effects. */
  lastMessageDone: MessageDoneInfo | null;
}

export const initialChatLiveState: ChatLiveState = {
  streamingText: null,
  streamingRag: null,
  toolEvents: [],
  subAgents: {},
  patternLines: [],
  approvals: [],
  error: null,
  lastMessageDone: null,
};

export type ChatLiveAction =
  | ServerEvent
  | { type: "reset" }
  | { type: "clear_streaming_text" }
  | { type: "reset_for_new_turn" }
  | { type: "reset_for_edit_or_regenerate" }
  | { type: "remove_approval_locally"; approval_id: string }
  | { type: "seed_approvals"; approvals: Approval[] }
  | { type: "external_error"; message: string };

interface ReducerCtx {
  currentId: string | null;
}

export function chatLiveReducer(
  state: ChatLiveState,
  action: ChatLiveAction,
  ctx: ReducerCtx,
): ChatLiveState {
  switch (action.type) {
    case "reset":
      return initialChatLiveState;
    case "clear_streaming_text":
      return { ...state, streamingText: null };
    case "reset_for_new_turn":
      return {
        ...state,
        streamingText: null,
        streamingRag: null,
        subAgents: {},
        patternLines: [],
        error: null,
      };
    case "reset_for_edit_or_regenerate":
      return {
        ...state,
        toolEvents: [],
        approvals: [],
        streamingText: null,
        error: null,
      };
    case "remove_approval_locally":
      return { ...state, approvals: state.approvals.filter((a) => a.id !== action.approval_id) };
    case "seed_approvals":
      // Merge rather than replace — a WS `approval_required` can race ahead of
      // the initial REST fetch this seeds from; replacing could drop it.
      return {
        ...state,
        approvals: [
          ...state.approvals,
          ...action.approvals.filter((a) => !state.approvals.some((x) => x.id === a.id)),
        ],
      };
    case "external_error":
      return { ...state, error: action.message };
    default:
      return reduceServerEvent(state, action, ctx);
  }
}

function reduceServerEvent(
  state: ChatLiveState,
  event: ServerEvent,
  ctx: ReducerCtx,
): ChatLiveState {
  switch (event.type) {
    case "token":
      return { ...state, streamingText: (state.streamingText ?? "") + event.content };

    case "rag_retrieval":
      return { ...state, streamingRag: { count: event.chunk_count, ms: event.total_ms } };

    case "rag_no_context":
      return { ...state, streamingRag: { count: 0, ms: 0, no_context: true, reason: event.reason } };

    case "tool_call_start":
      return {
        ...state,
        toolEvents: [
          ...state.toolEvents,
          { tool_call_id: event.tool_call_id, tool_name: event.tool_name, status: "running" },
        ],
      };

    case "tool_call_result":
      return {
        ...state,
        toolEvents: state.toolEvents.map((t) =>
          t.tool_call_id === event.tool_call_id ? { ...t, status: "done", result: event.result } : t,
        ),
      };

    case "approval_required":
      return state.approvals.some((a) => a.id === event.approval_id)
        ? state
        : {
            ...state,
            approvals: [
              ...state.approvals,
              {
                id: event.approval_id,
                session_id: ctx.currentId ?? "",
                tool_call_id: event.tool_call_id,
                tool_name: event.tool_name,
                tool_args: event.args,
                status: "pending",
              },
            ],
          };

    case "approval_resolved":
      return { ...state, approvals: state.approvals.filter((a) => a.id !== event.approval_id) };

    case "message_done":
      return {
        ...state,
        streamingText: null,
        streamingRag: null,
        toolEvents: [],
        lastMessageDone: {
          messageId: event.message_id,
          content: state.streamingText ?? "",
          hadToolCalls: state.toolEvents.length > 0,
          tokensIn: event.tokens_in,
          tokensOut: event.tokens_out,
        },
      };

    case "subagent_started":
      return {
        ...state,
        subAgents: {
          ...state.subAgents,
          [event.sub_session_id]: {
            subSessionId: event.sub_session_id,
            agentName: event.agent_name,
            depth: event.depth,
            task: event.task,
            status: "running",
            lines: [],
          },
        },
      };

    case "subagent_done": {
      const cur = state.subAgents[event.sub_session_id];
      if (!cur) return state;
      return {
        ...state,
        subAgents: {
          ...state.subAgents,
          [event.sub_session_id]: {
            ...cur,
            status: event.status === "ok" ? "ok" : "empty",
            tokensIn: event.tokens_in,
            tokensOut: event.tokens_out,
            costUsd: event.cost_usd,
          },
        },
      };
    }

    case "subagent_limit":
      return { ...state, patternLines: [...state.patternLines, `⚠ limit ${event.limit}: ${event.detail}`] };

    case "subagent": {
      let next = state;
      const inner = event.event;
      const line =
        inner.type === "token"
          ? null
          : inner.type === "tool_call_start"
            ? `→ ${inner.tool_name}`
            : inner.type === "approval_required"
              ? `⏸ awaiting approval: ${inner.tool_name}`
              : inner.type === "message_done"
                ? "✓ answered"
                : inner.type;

      if (line) {
        const cur = next.subAgents[event.sub_session_id];
        if (cur) {
          next = {
            ...next,
            subAgents: {
              ...next.subAgents,
              [event.sub_session_id]: { ...cur, lines: [...cur.lines, line] },
            },
          };
        }
      }

      if (inner.type === "approval_required") {
        next = next.approvals.some((a) => a.id === inner.approval_id)
          ? next
          : {
              ...next,
              approvals: [
                ...next.approvals,
                {
                  id: inner.approval_id,
                  session_id: event.sub_session_id,
                  tool_call_id: inner.tool_call_id,
                  tool_name: inner.tool_name,
                  tool_args: inner.args,
                  status: "pending",
                  sub_session_id: event.sub_session_id,
                  agent_name: event.agent_name,
                },
              ],
            };
      }
      if (inner.type === "approval_resolved") {
        next = { ...next, approvals: next.approvals.filter((a) => a.id !== inner.approval_id) };
      }
      return next;
    }

    case "plan_created":
      return { ...state, patternLines: [...state.patternLines, `plan: ${event.steps.length} step(s)`] };
    case "step_started":
      return { ...state, patternLines: [...state.patternLines, `▸ step ${event.step}: ${event.goal}`] };
    case "step_done":
      return { ...state, patternLines: [...state.patternLines, `  step ${event.step}: ${event.status}`] };
    case "reflection":
      return {
        ...state,
        patternLines: [
          ...state.patternLines,
          event.lesson
            ? `reflect #${event.attempt}: ${event.lesson}`
            : `eval #${event.attempt}: score ${event.score ?? "?"}`,
        ],
      };
    case "route_decision":
      return {
        ...state,
        patternLines: [
          ...state.patternLines,
          `route → ${event.route ?? "(fallback)"} (${event.confidence.toFixed(2)})`,
        ],
      };
    case "worker_started":
      return { ...state, patternLines: [...state.patternLines, `worker ${event.agent_name} started`] };
    case "worker_result":
      return { ...state, patternLines: [...state.patternLines, `worker ${event.agent_name}: ${event.status}`] };
    case "debate_vote":
      return { ...state, patternLines: [...state.patternLines, `judge → ${event.winner}`] };
    case "ab_arm":
      return { ...state, patternLines: [...state.patternLines, `A/B → running challenger: ${event.pattern}`] };

    case "error":
      return { ...state, error: event.message, streamingText: null, streamingRag: null };

    default:
      return state;
  }
}
