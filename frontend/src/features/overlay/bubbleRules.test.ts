import { describe, expect, it } from "vitest";
import type { Approval } from "@/api/client";
import {
  BUBBLE_MAX_CHARS,
  hasBlockContent,
  overlayMode,
  pickBubble,
  toBubbleText,
  type BubbleInput,
} from "./bubbleRules";

const approval: Approval = {
  id: "a1",
  session_id: "s",
  tool_call_id: "t",
  tool_name: "execute_code",
  tool_args: {},
  status: "pending",
};

const none: BubbleInput = {
  approvals: [],
  error: null,
  errorVisible: false,
  streamingText: null,
  runningTool: null,
  doneText: null,
};

describe("bubble text (SPEC §21.4.2)", () => {
  it("shows a short plain reply whole", () => {
    expect(toBubbleText("Xin chào!")).toEqual({ text: "Xin chào!", truncated: false });
  });

  it("cuts a long reply near the limit, on a word boundary, and flags it", () => {
    const long = "từ ".repeat(200);
    const r = toBubbleText(long);
    expect(r.truncated).toBe(true);
    expect(r.text.length).toBeLessThanOrEqual(BUBBLE_MAX_CHARS);
    expect(r.text.endsWith(" ")).toBe(false);
  });

  it("a code block is never shown inline, even in a short reply", () => {
    const r = toBubbleText("Đây là code:\n```py\nprint(1)\n```");
    expect(r.truncated).toBe(true);
    expect(r.text).toBe("Đây là code:");
    expect(r.text).not.toContain("```");
  });

  it("a table is dropped, keeping the text before it", () => {
    const md = "So sánh:\n\n| a | b |\n|---|---|\n| 1 | 2 |";
    const r = toBubbleText(md);
    expect(r.truncated).toBe(true);
    expect(r.text).toBe("So sánh:");
  });

  it("detects block content", () => {
    expect(hasBlockContent("plain text | with a pipe")).toBe(false);
    expect(hasBlockContent("```x```")).toBe(true);
    expect(hasBlockContent("| a | b |\n| - | - |")).toBe(true);
  });
});

describe("bubble priority (SPEC §21.4.2)", () => {
  it("is empty when there is nothing to show", () => {
    expect(pickBubble(none)).toBeNull();
  });

  it("an approval card beats everything, including streaming text and errors", () => {
    const b = pickBubble({
      ...none,
      approvals: [approval],
      error: "boom",
      errorVisible: true,
      streamingText: "hi",
      doneText: "old",
    });
    expect(b?.kind).toBe("approval");
  });

  it("error beats streaming; streaming beats tool label; tool label beats finished reply", () => {
    expect(pickBubble({ ...none, error: "x", errorVisible: true, streamingText: "s" })?.kind).toBe("error");
    expect(pickBubble({ ...none, streamingText: "s", runningTool: "t", doneText: "d" })).toMatchObject({
      kind: "text",
      streaming: true,
    });
    expect(pickBubble({ ...none, runningTool: "write_file", doneText: "d" })?.kind).toBe("tool");
    expect(pickBubble({ ...none, doneText: "d" })).toMatchObject({ kind: "text", streaming: false });
  });

  it("an expired error is not shown", () => {
    expect(pickBubble({ ...none, error: "x", errorVisible: false })).toBeNull();
  });

  it("the tool label never exposes the tool args", () => {
    const b = pickBubble({ ...none, runningTool: "write_file" });
    expect(b).toEqual({ kind: "tool", label: "Đang dùng write_file…" });
  });
});

describe("window mode (SPEC §21.3.1)", () => {
  it("panel wins; otherwise bubble when something is shown, else compact", () => {
    const text = pickBubble({ ...none, doneText: "hi" });
    expect(overlayMode(true, text)).toBe("panel");
    expect(overlayMode(false, text)).toBe("bubble");
    expect(overlayMode(false, null)).toBe("compact");
  });
});
