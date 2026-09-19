// Click-through decision for the overlay window (SPEC §21.3.2). Kept free of
// React/Electron so it is unit-testable: the window ignores the mouse by
// default and only becomes interactive over marked elements or the model box.

export const HIT_ATTR = "data-overlay-hit";
export const MODEL_ATTR = "data-overlay-model";

/** Fraction of the model container trimmed from each side to approximate the
 * model's bounding box (v1 accepts a box, not per-pixel alpha — SPEC §21.16). */
export const MODEL_INSET = { x: 0.2, y: 0.04 };

export interface Point {
  x: number;
  y: number;
}

export interface Box {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

export function insetBox(box: Box, inset = MODEL_INSET): Box {
  const w = box.right - box.left;
  const h = box.bottom - box.top;
  return {
    left: box.left + w * inset.x,
    right: box.right - w * inset.x,
    top: box.top + h * inset.y,
    bottom: box.bottom - h * inset.y,
  };
}

export function pointInBox(p: Point, box: Box): boolean {
  return p.x >= box.left && p.x <= box.right && p.y >= box.top && p.y <= box.bottom;
}

export interface InteractiveInput {
  point: Point;
  /** element under the pointer, from document.elementFromPoint */
  target: Element | null;
  /** bounding box of the model container, if mounted */
  modelBox: Box | null;
  /** input focused, dragging, dialog open… (SPEC §21.3.2) */
  forced: boolean;
}

export function computeInteractive({ point, target, modelBox, forced }: InteractiveInput): boolean {
  if (forced) return true;
  if (target?.closest(`[${HIT_ATTR}]`)) return true;
  if (modelBox && pointInBox(point, insetBox(modelBox))) return true;
  return false;
}
