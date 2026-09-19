import { describe, expect, it } from "vitest";
import { computeInteractive, insetBox, pointInBox } from "./hitTest";

const modelBox = { left: 0, top: 0, right: 100, bottom: 100 };

describe("overlay click-through (SPEC §21.3.2)", () => {
  it("is not interactive over empty space", () => {
    expect(computeInteractive({ point: { x: 5, y: 50 }, target: null, modelBox, forced: false })).toBe(false);
  });

  it("is interactive inside the (inset) model box", () => {
    expect(computeInteractive({ point: { x: 50, y: 50 }, target: null, modelBox, forced: false })).toBe(true);
  });

  it("the inset trims the transparent margin around the model", () => {
    const inner = insetBox(modelBox);
    expect(pointInBox({ x: 5, y: 50 }, inner)).toBe(false);
    expect(pointInBox({ x: 50, y: 50 }, inner)).toBe(true);
  });

  it("is interactive over an element marked data-overlay-hit (or its children)", () => {
    document.body.innerHTML = `<div data-overlay-hit><span id="c">x</span></div><div id="o"></div>`;
    const child = document.getElementById("c");
    const other = document.getElementById("o");
    expect(computeInteractive({ point: { x: 5, y: 50 }, target: child, modelBox: null, forced: false })).toBe(true);
    expect(computeInteractive({ point: { x: 5, y: 50 }, target: other, modelBox: null, forced: false })).toBe(false);
  });

  it("forced (input focus / drag / capture) always wins", () => {
    expect(computeInteractive({ point: { x: 5, y: 50 }, target: null, modelBox: null, forced: true })).toBe(true);
  });
});
