import { describe, expect, it } from "vitest";
import type { ServerEvent } from "./ws";

// Guard the reducer-ish shape our Chat page relies on.
describe("ServerEvent shapes", () => {
  it("token event carries incremental content", () => {
    const e: ServerEvent = { type: "token", message_id: "m1", content: "hi" };
    expect(e.type).toBe("token");
    if (e.type === "token") expect(e.content).toBe("hi");
  });

  it("approval_required carries tool name + args", () => {
    const e: ServerEvent = {
      type: "approval_required",
      approval_id: "a1",
      tool_call_id: "t1",
      tool_name: "execute_code",
      args: { language: "python", code: "print(1)" },
    };
    if (e.type === "approval_required") {
      expect(e.tool_name).toBe("execute_code");
      expect(e.args.language).toBe("python");
    }
  });
});

describe("reconnect backoff (SPEC §21.10)", () => {
  it("starts at 1.5s, doubles, and stops at the 30s ceiling", async () => {
    const { reconnectDelay } = await import("./ws");
    expect([0, 1, 2, 3, 4].map(reconnectDelay)).toEqual([1500, 3000, 6000, 12000, 24000]);
    expect(reconnectDelay(5)).toBe(30000);
    expect(reconnectDelay(50)).toBe(30000);
  });
});
