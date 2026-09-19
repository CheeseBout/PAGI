import { describe, expect, it } from "vitest";
import { parseScreenCommand } from "./screenCommand";

describe("/screen command (SPEC §21.9)", () => {
  it("recognises the bare command and the command with a question", () => {
    expect(parseScreenCommand("/screen")).toEqual({ isCommand: true, rest: "" });
    expect(parseScreenCommand("  /screen  ")).toEqual({ isCommand: true, rest: "" });
    expect(parseScreenCommand("/screen giải thích lỗi này")).toEqual({ isCommand: true, rest: "giải thích lỗi này" });
    expect(parseScreenCommand("/SCREEN what is this?")).toEqual({ isCommand: true, rest: "what is this?" });
  });

  it("keeps a multi-line question intact", () => {
    expect(parseScreenCommand("/screen dòng 1\ndòng 2").rest).toBe("dòng 1\ndòng 2");
  });

  it("is not triggered by ordinary text, look-alikes or mid-sentence mentions", () => {
    for (const t of ["hello", "/screens", "/screenshot", "xem /screen", "screen", "//screen", ""]) {
      expect(parseScreenCommand(t).isCommand).toBe(false);
    }
  });

  it("returns the original text untouched when it is not a command", () => {
    expect(parseScreenCommand("xin chào").rest).toBe("xin chào");
  });
});
