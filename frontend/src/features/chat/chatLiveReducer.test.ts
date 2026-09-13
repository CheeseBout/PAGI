import { describe, expect, it } from "vitest";
import { chatLiveReducer, initialChatLiveState, type ChatLiveState } from "./chatLiveReducer";

const ctx = { currentId: "sess-1" };
const reduce = (state: ChatLiveState, action: Parameters<typeof chatLiveReducer>[1]) =>
  chatLiveReducer(state, action, ctx);

describe("chatLiveReducer", () => {
  it("accumulates streamed tokens", () => {
    let s = initialChatLiveState;
    s = reduce(s, { type: "token", message_id: "m1", content: "Hel" });
    s = reduce(s, { type: "token", message_id: "m1", content: "lo" });
    expect(s.streamingText).toBe("Hello");
  });

  it("tracks rag retrieval and no-context", () => {
    let s = initialChatLiveState;
    s = reduce(s, {
      type: "rag_retrieval",
      query_log_id: "q1",
      chunk_count: 3,
      total_ms: 42,
      citations: [],
    });
    expect(s.streamingRag).toEqual({ count: 3, ms: 42 });

    s = reduce(s, { type: "rag_no_context", query_log_id: null, reason: "no_hits" });
    expect(s.streamingRag).toEqual({ count: 0, ms: 0, no_context: true, reason: "no_hits" });
  });

  it("tracks tool call lifecycle", () => {
    let s = initialChatLiveState;
    s = reduce(s, {
      type: "tool_call_start",
      tool_call_id: "tc1",
      tool_name: "write_file",
      args: { path: "a.txt" },
    });
    expect(s.toolEvents).toEqual([{ tool_call_id: "tc1", tool_name: "write_file", status: "running" }]);

    s = reduce(s, { type: "tool_call_result", tool_call_id: "tc1", result: { bytes_written: 5 } });
    expect(s.toolEvents).toEqual([
      { tool_call_id: "tc1", tool_name: "write_file", status: "done", result: { bytes_written: 5 } },
    ]);
  });

  it("dedupes approval_required by id and removes on approval_resolved", () => {
    let s = initialChatLiveState;
    const event = {
      type: "approval_required" as const,
      approval_id: "ap1",
      tool_call_id: "tc1",
      tool_name: "execute_code",
      args: {},
    };
    s = reduce(s, event);
    s = reduce(s, event); // duplicate frame — must not double-add
    expect(s.approvals).toHaveLength(1);
    expect(s.approvals[0].session_id).toBe("sess-1");

    s = reduce(s, { type: "approval_resolved", approval_id: "ap1", status: "approved" });
    expect(s.approvals).toHaveLength(0);
  });

  it("captures message_done content and clears the live streaming state", () => {
    let s = initialChatLiveState;
    s = reduce(s, { type: "token", message_id: "m1", content: "Hi there" });
    s = reduce(s, {
      type: "tool_call_start",
      tool_call_id: "tc1",
      tool_name: "fetch_url",
      args: {},
    });
    s = reduce(s, {
      type: "message_done",
      message_id: "m1",
      tokens_in: 10,
      tokens_out: 5,
    });
    expect(s.streamingText).toBeNull();
    expect(s.toolEvents).toEqual([]);
    expect(s.lastMessageDone).toEqual({
      messageId: "m1",
      content: "Hi there",
      hadToolCalls: true,
      tokensIn: 10,
      tokensOut: 5,
    });
  });

  it("marks hadToolCalls false for a plain text-only turn", () => {
    let s = initialChatLiveState;
    s = reduce(s, { type: "token", message_id: "m1", content: "plain answer" });
    s = reduce(s, { type: "message_done", message_id: "m1", tokens_in: 1, tokens_out: 1 });
    expect(s.lastMessageDone).toEqual({
      messageId: "m1",
      content: "plain answer",
      hadToolCalls: false,
      tokensIn: 1,
      tokensOut: 1,
    });
  });

  it("bubbles a nested sub-agent approval into the shared approvals list, labelled", () => {
    let s = initialChatLiveState;
    s = reduce(s, {
      type: "subagent_started",
      sub_session_id: "sub1",
      agent_name: "Researcher",
      task: "look things up",
      depth: 1,
    });
    s = reduce(s, {
      type: "subagent",
      sub_session_id: "sub1",
      agent_name: "Researcher",
      depth: 1,
      event: {
        type: "approval_required",
        approval_id: "ap-sub",
        tool_call_id: "tc-sub",
        tool_name: "execute_code",
        args: {},
      },
    });
    expect(s.approvals).toHaveLength(1);
    expect(s.approvals[0]).toMatchObject({ id: "ap-sub", sub_session_id: "sub1", agent_name: "Researcher" });
    expect(s.subAgents.sub1.lines).toEqual(["⏸ awaiting approval: execute_code"]);

    s = reduce(s, {
      type: "subagent",
      sub_session_id: "sub1",
      agent_name: "Researcher",
      depth: 1,
      event: { type: "approval_resolved", approval_id: "ap-sub", status: "approved" },
    });
    expect(s.approvals).toHaveLength(0);
  });

  it("clears streaming state on error", () => {
    let s = initialChatLiveState;
    s = reduce(s, { type: "token", message_id: "m1", content: "partial" });
    s = reduce(s, { type: "error", code: "provider_error", message: "boom" });
    expect(s.error).toBe("boom");
    expect(s.streamingText).toBeNull();
  });

  it("resets to initial state", () => {
    let s = initialChatLiveState;
    s = reduce(s, { type: "token", message_id: "m1", content: "x" });
    s = reduce(s, { type: "reset" });
    expect(s).toEqual(initialChatLiveState);
  });

  it("merges seeded approvals without clobbering ones already added via WS", () => {
    let s = initialChatLiveState;
    s = reduce(s, {
      type: "approval_required",
      approval_id: "ap-live",
      tool_call_id: "tc1",
      tool_name: "execute_code",
      args: {},
    });
    s = reduce(s, {
      type: "seed_approvals",
      approvals: [
        { id: "ap-rest", session_id: "sess-1", tool_call_id: "tc2", tool_name: "write_file", tool_args: {}, status: "pending" },
      ],
    });
    expect(s.approvals.map((a) => a.id).sort()).toEqual(["ap-live", "ap-rest"]);
  });

  it("removes an approval locally without waiting for the server event", () => {
    let s = initialChatLiveState;
    s = reduce(s, {
      type: "approval_required",
      approval_id: "ap1",
      tool_call_id: "tc1",
      tool_name: "execute_code",
      args: {},
    });
    s = reduce(s, { type: "remove_approval_locally", approval_id: "ap1" });
    expect(s.approvals).toHaveLength(0);
  });
});
