import { describe, expect, it } from "vitest";
import {
  initialAvatarReducerState,
  nextOnEvent,
  nextOnIdleTimeout,
  nextOnSendPulse,
  type AvatarReducerState,
} from "./avatarStateReducer";

describe("avatarStateReducer", () => {
  it("send pulse -> thinking, arms the timer", () => {
    const { state, timer } = nextOnSendPulse(initialAvatarReducerState);
    expect(state.aiState).toBe("thinking");
    expect(timer).toBe("arm");
  });

  it("token -> talking", () => {
    const { state } = nextOnEvent(initialAvatarReducerState, {
      type: "token",
      message_id: "m1",
      content: "hi",
    });
    expect(state.aiState).toBe("talking");
  });

  it("tool_call_start -> acting, takes priority over talking", () => {
    const talking: AvatarReducerState = { aiState: "talking", restoreAfterWaiting: "thinking" };
    const { state } = nextOnEvent(talking, {
      type: "tool_call_start",
      tool_call_id: "tc1",
      tool_name: "write_file",
      args: {},
    });
    expect(state.aiState).toBe("acting");
  });

  it("tool_call_result -> thinking", () => {
    const acting: AvatarReducerState = { aiState: "acting", restoreAfterWaiting: "thinking" };
    const { state } = nextOnEvent(acting, {
      type: "tool_call_result",
      tool_call_id: "tc1",
      result: {},
    });
    expect(state.aiState).toBe("thinking");
  });

  it("approval_required overrides talking, and approval_resolved restores it", () => {
    const talking: AvatarReducerState = { aiState: "talking", restoreAfterWaiting: "thinking" };
    const required = nextOnEvent(talking, {
      type: "approval_required",
      approval_id: "a1",
      tool_call_id: "tc1",
      tool_name: "delete_file",
      args: {},
    });
    expect(required.state.aiState).toBe("waiting");
    expect(required.timer).toBe("clear");
    expect(required.state.restoreAfterWaiting).toBe("talking");

    const resolved = nextOnEvent(required.state, {
      type: "approval_resolved",
      approval_id: "a1",
      status: "approved",
    });
    expect(resolved.state.aiState).toBe("talking");
  });

  it("approval_required from idle restores to thinking, not idle", () => {
    const required = nextOnEvent(initialAvatarReducerState, {
      type: "approval_required",
      approval_id: "a1",
      tool_call_id: "tc1",
      tool_name: "delete_file",
      args: {},
    });
    expect(required.state.restoreAfterWaiting).toBe("thinking");
  });

  it("a second approval_required while already waiting keeps the original restore target", () => {
    const waiting: AvatarReducerState = { aiState: "waiting", restoreAfterWaiting: "acting" };
    const { state } = nextOnEvent(waiting, {
      type: "approval_required",
      approval_id: "a2",
      tool_call_id: "tc2",
      tool_name: "delete_file",
      args: {},
    });
    expect(state.restoreAfterWaiting).toBe("acting");
  });

  it("message_done and error force idle and clear the timer", () => {
    const talking: AvatarReducerState = { aiState: "talking", restoreAfterWaiting: "thinking" };
    expect(
      nextOnEvent(talking, { type: "message_done", message_id: "m1", tokens_in: 1, tokens_out: 1 }).state
        .aiState,
    ).toBe("idle");
    expect(nextOnEvent(talking, { type: "error", code: "boom", message: "x" }).state.aiState).toBe("idle");
  });

  it("token while waiting for approval does not resume talking", () => {
    const waiting: AvatarReducerState = { aiState: "waiting", restoreAfterWaiting: "thinking" };
    const { state } = nextOnEvent(waiting, { type: "token", message_id: "m1", content: "x" });
    expect(state.aiState).toBe("waiting");
  });

  it("an unmapped event (e.g. subagent) is a no-op", () => {
    const { state, timer } = nextOnEvent(initialAvatarReducerState, {
      type: "subagent_limit",
      limit: "depth",
      detail: "x",
      sub_session_id: null,
    });
    expect(state).toBe(initialAvatarReducerState);
    expect(timer).toBe("none");
  });

  it("idle timeout forces idle regardless of prior state", () => {
    const acting: AvatarReducerState = { aiState: "acting", restoreAfterWaiting: "thinking" };
    expect(nextOnIdleTimeout(acting).aiState).toBe("idle");
  });
});
