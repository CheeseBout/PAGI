import { describe, expect, it } from "vitest";
import { diffStats, lineDiff } from "./linediff";

describe("lineDiff", () => {
  it("marks added and removed lines around a common block", () => {
    const rows = lineDiff("a\nb\nc", "a\nB\nc\nd");
    expect(rows.map((r) => `${r.kind}:${r.text}`)).toEqual([
      "same:a",
      "del:b",
      "add:B",
      "same:c",
      "add:d",
    ]);
    expect(diffStats(rows)).toEqual({ added: 2, removed: 1 });
  });

  it("treats a null old side as an all-additions new file", () => {
    const rows = lineDiff(null, "x\ny");
    expect(rows.every((r) => r.kind === "add")).toBe(true);
  });

  it("returns all 'same' rows for identical input", () => {
    const rows = lineDiff("one\ntwo", "one\ntwo");
    expect(rows.every((r) => r.kind === "same")).toBe(true);
  });
});
