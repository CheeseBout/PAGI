import { describe, expect, it } from "vitest";
import { ACTIVE_FPS, IDLE_FPS, IDLE_SLOWDOWN_MS, fpsFor, idleMsFromSearch } from "./idleSlowdown";

describe("idle frame-rate policy (SPEC §21.12)", () => {
  it("runs at the full 30fps cap while anything is happening", () => {
    expect(fpsFor({ busy: true, sinceInputMs: 10 * IDLE_SLOWDOWN_MS })).toBe(ACTIVE_FPS);
  });

  it("stays at full rate until the idle threshold, then drops to 15fps", () => {
    expect(fpsFor({ busy: false, sinceInputMs: IDLE_SLOWDOWN_MS - 1 })).toBe(ACTIVE_FPS);
    expect(fpsFor({ busy: false, sinceInputMs: IDLE_SLOWDOWN_MS })).toBe(IDLE_FPS);
    expect(IDLE_SLOWDOWN_MS).toBe(30_000);
    expect(IDLE_FPS).toBeLessThan(ACTIVE_FPS);
  });

  it("any input (sinceInputMs back to 0) restores full rate", () => {
    expect(fpsFor({ busy: false, sinceInputMs: 0 })).toBe(ACTIVE_FPS);
  });

  it("the threshold is configurable", () => {
    expect(fpsFor({ busy: false, sinceInputMs: 3000, idleMs: 2000 })).toBe(IDLE_FPS);
    expect(fpsFor({ busy: false, sinceInputMs: 1000, idleMs: 2000 })).toBe(ACTIVE_FPS);
  });

  it("?idleMs= overrides the threshold only for sane values", () => {
    expect(idleMsFromSearch("?idleMs=2000")).toBe(2000);
    expect(idleMsFromSearch("")).toBe(IDLE_SLOWDOWN_MS);
    expect(idleMsFromSearch("?idleMs=abc")).toBe(IDLE_SLOWDOWN_MS);
    expect(idleMsFromSearch("?idleMs=10")).toBe(IDLE_SLOWDOWN_MS); // too small to be intentional
  });
});
