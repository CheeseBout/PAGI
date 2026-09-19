// Pure transition logic for the avatar state machine (SPEC §20.6), split out
// of useAvatarState.ts so it's unit-testable without a React renderer — same
// split as features/chat/chatLiveReducer.ts vs useChatSession.ts.
import type { ServerEvent } from "@/api/ws";
import type { AiState } from "@/components/Avatar/AvatarCanvas";

export interface AvatarReducerState {
  aiState: AiState;
  /** state to return to on approval_resolved. */
  restoreAfterWaiting: AiState;
}

export const initialAvatarReducerState: AvatarReducerState = {
  aiState: "idle",
  restoreAfterWaiting: "thinking",
};

/** What the IDLE_TIMEOUT_MS safety-net timer should do after this transition. */
export type TimerAction = "arm" | "clear" | "none";

export function nextOnSendPulse(
  state: AvatarReducerState,
): { state: AvatarReducerState; timer: TimerAction } {
  return { state: { ...state, aiState: "thinking" }, timer: "arm" };
}

export function nextOnIdleTimeout(state: AvatarReducerState): AvatarReducerState {
  return { ...state, aiState: "idle" };
}

export function nextOnEvent(
  state: AvatarReducerState,
  event: ServerEvent,
): { state: AvatarReducerState; timer: TimerAction } {
  switch (event.type) {
    case "token":
      return {
        state: state.aiState === "waiting" ? state : { ...state, aiState: "talking" },
        timer: "arm",
      };
    case "tool_call_start":
      return { state: { ...state, aiState: "acting" }, timer: "arm" };
    case "tool_call_result":
      return {
        state: state.aiState === "waiting" ? state : { ...state, aiState: "thinking" },
        timer: "arm",
      };
    case "approval_required": {
      // capture what to restore to, unless we're already waiting (nested/
      // repeated approval_required — keep the original pre-waiting state).
      const restore =
        state.aiState === "waiting"
          ? state.restoreAfterWaiting
          : state.aiState === "idle"
            ? "thinking"
            : state.aiState;
      return { state: { aiState: "waiting", restoreAfterWaiting: restore }, timer: "clear" };
    }
    case "approval_resolved":
      return { state: { ...state, aiState: state.restoreAfterWaiting }, timer: "arm" };
    case "message_done":
    case "error":
      return { state: { ...state, aiState: "idle" }, timer: "clear" };
    default:
      // subagent/orchestration events etc. don't drive the root agent's
      // avatar in v1 (§20.6) — deliberately ignored.
      return { state, timer: "none" };
  }
}
