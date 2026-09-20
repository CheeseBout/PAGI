import { describe, expect, it } from "vitest";
import { CubismMatrix44 } from "@framework/math/cubismmatrix44";
import {
  canReactToTouch,
  canTap,
  clientPointToNdc,
  eyeAnchorModelY,
  invertMvpPoint,
  isTap,
  lookTarget,
  modelYExtent,
  type DrawableSource,
  TAP_COOLDOWN_MS,
  TAP_HOLD_MS,
  TAP_MOVE_PX,
} from "./avatarPointer";

describe("clientPointToNdc", () => {
  const rect = { left: 100, top: 50, width: 200, height: 100 };

  it("maps the canvas center to (0, 0)", () => {
    // toBeCloseTo, not toEqual: the Y flip produces -0 at dead center, which
    // is numerically 0 but fails strict object equality.
    const { x, y } = clientPointToNdc(200, 100, rect);
    expect(x).toBeCloseTo(0);
    expect(y).toBeCloseTo(0);
  });

  it("maps top-left to (-1, 1) — Y is flipped to Cubism's up-positive convention", () => {
    expect(clientPointToNdc(100, 50, rect)).toEqual({ x: -1, y: 1 });
  });

  it("maps bottom-right to (1, -1)", () => {
    expect(clientPointToNdc(300, 150, rect)).toEqual({ x: 1, y: -1 });
  });

  it("clamps points outside the canvas instead of extrapolating", () => {
    expect(clientPointToNdc(1000, 1000, rect)).toEqual({ x: 1, y: -1 });
    expect(clientPointToNdc(-1000, -1000, rect)).toEqual({ x: -1, y: 1 });
  });
});

describe("invertMvpPoint (SPEC §20.14.3)", () => {
  it("round-trips through a non-identity scale+offset MVP", () => {
    // Mirrors what AvatarCanvas actually builds: scale(cfg.scale) + a pixel
    // offset — never the identity matrix, which would hide a wrong formula.
    const mvp = new CubismMatrix44();
    mvp.scale(2, 3);
    mvp.translate(10, -5);
    // forward: modelX*2+10=src -> src=14 for modelX=2; verify inverse recovers it
    expect(invertMvpPoint(mvp, { x: 14, y: -5 })).toEqual({ x: 2, y: 0 });
  });

  it("differs from the identity case — catches a formula that ignores scale/offset entirely", () => {
    const identity = new CubismMatrix44();
    const scaled = new CubismMatrix44();
    scaled.scale(2, 2);
    scaled.translate(1, 1);
    const ndc = { x: 0.5, y: 0.5 };
    expect(invertMvpPoint(scaled, ndc)).not.toEqual(invertMvpPoint(identity, ndc));
  });
});

describe("isTap (SPEC §20.14.5)", () => {
  it("small, quick movement is a tap", () => {
    expect(isTap(1, 1, 50)).toBe(true);
  });

  it("a real drag — far in screen space — is never a tap, however short", () => {
    expect(isTap(300, 0, 10)).toBe(false);
  });

  it("boundary: movement right at TAP_MOVE_PX is rejected (strict <)", () => {
    expect(isTap(TAP_MOVE_PX, 0, 10)).toBe(false);
  });

  it("held too long is not a tap even without moving", () => {
    expect(isTap(0, 0, TAP_HOLD_MS)).toBe(false);
  });

  it("MUST be evaluated on screen deltas: an overlay window-drag keeps client position ~still but screen position moves far — screen deltas correctly say 'not a tap'", () => {
    // This is the exact failure mode SPEC §20.14.5 calls out: while dragging
    // the overlay window, the window follows the cursor, so if this helper
    // were fed client-space deltas instead of screen-space ones, a 300px
    // window drag would misclassify as a tap.
    const screenDx = 300;
    const screenDy = 0;
    expect(isTap(screenDx, screenDy, 250)).toBe(false);
  });
});

describe("canReactToTouch (SPEC §20.14.1)", () => {
  it("only idle allows hover/tap reactions", () => {
    expect(canReactToTouch("idle")).toBe(true);
  });

  it("busy states block touch reactions, look-follow is unaffected by this gate", () => {
    expect(canReactToTouch("thinking")).toBe(false);
    expect(canReactToTouch("talking")).toBe(false);
    expect(canReactToTouch("acting")).toBe(false);
    expect(canReactToTouch("waiting")).toBe(false);
  });
});

describe("canTap / cooldown (SPEC §20.14.4)", () => {
  it("first tap ever (lastReactionAt = -Infinity) is always allowed", () => {
    expect(canTap(-Infinity, 0)).toBe(true);
  });

  it("a second tap inside the cooldown window is dropped", () => {
    expect(canTap(1000, 1000 + TAP_COOLDOWN_MS - 1)).toBe(false);
  });

  it("a tap exactly at the cooldown boundary is allowed", () => {
    expect(canTap(1000, 1000 + TAP_COOLDOWN_MS)).toBe(true);
  });

  it("a tap well after the cooldown is allowed", () => {
    expect(canTap(1000, 1000 + TAP_COOLDOWN_MS + 5000)).toBe(true);
  });
});

describe("lookTarget (SPEC §20.14.2 eye anchor)", () => {
  it("a cursor level with the eyes means straight ahead, not 'look up'", () => {
    expect(lookTarget({ x: 0.3, y: 0.7 }, 0.7)).toEqual({ x: 0.3, y: 0 });
  });

  it("canvas top and bottom still reach full deflection", () => {
    expect(lookTarget({ x: 0, y: 1 }, 0.7).y).toBeCloseTo(1);
    expect(lookTarget({ x: 0, y: -1 }, 0.7).y).toBeCloseTo(-1);
  });

  it("a cursor at the old anchor (canvas centre) now reads as looking down", () => {
    expect(lookTarget({ x: 0, y: 0 }, 0.7).y).toBeLessThan(-0.3);
  });

  it("anchor 0 (unknown / centred model) is the identity", () => {
    expect(lookTarget({ x: -0.4, y: 0.5 }, 0)).toEqual({ x: -0.4, y: 0.5 });
  });

  it("a degenerate anchor near the edge cannot divide by ~0", () => {
    const t = lookTarget({ x: 0, y: 1 }, 1);
    expect(Number.isFinite(t.y)).toBe(true);
    expect(Math.abs(t.y)).toBeLessThanOrEqual(1);
  });
});

describe("modelYExtent / eyeAnchorModelY", () => {
  const drawable = (ys: number[], opts: { visible?: boolean; opacity?: number } = {}) => ({
    ys,
    visible: opts.visible ?? true,
    opacity: opts.opacity ?? 1,
  });
  const fake = (ds: ReturnType<typeof drawable>[], maskedBy: Record<number, number[]> = {}): DrawableSource => ({
    getDrawableCount: () => ds.length,
    getDrawableVertexCount: (i) => ds[i].ys.length,
    getDrawableVertices: (i) => Float32Array.from(ds[i].ys.flatMap((y) => [0, y])),
    getDrawableOpacity: (i) => ds[i].opacity,
    getDrawableDynamicFlagIsVisible: (i) => ds[i].visible,
    getDrawableMasks: () => ds.map((_, i) => Int32Array.from(maskedBy[i] ?? [])),
    getDrawableMaskCounts: () => Int32Array.from(ds.map((_, i) => (maskedBy[i] ?? []).length)),
  });

  it("spans every visible drawable", () => {
    expect(modelYExtent(fake([drawable([-1, 0.5]), drawable([0, 2])]))).toEqual({ minY: -1, maxY: 2 });
  });

  it("ignores hidden, transparent and clipping-mask drawables", () => {
    const src = fake(
      [drawable([0, 1]), drawable([-50, 50], { visible: false }), drawable([-60, 60], { opacity: 0 }), drawable([-70, 70])],
      { 0: [3] }, // drawable 0 is clipped by drawable 3 -> 3 is a mask mesh
    );
    expect(modelYExtent(src)).toEqual({ minY: 0, maxY: 1 });
  });

  it("returns null when nothing is drawn yet", () => {
    expect(modelYExtent(fake([drawable([0, 1], { opacity: 0 })]))).toBeNull();
  });

  it("puts the anchor 90% of the way up from the feet", () => {
    expect(eyeAnchorModelY({ minY: -1, maxY: 1 })).toBeCloseTo(0.8);
  });
});
