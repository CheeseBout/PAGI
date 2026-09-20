// Pure pointer-interaction helpers for AvatarCanvas (SPEC §20.14). Kept free
// of WebGL/Live2D framework calls (only CubismMatrix44's own, unmodified
// transform helpers are reused) so they're unit-testable in jsdom, which
// can't create a WebGL2 context.
import type { CubismMatrix44 } from "@framework/math/cubismmatrix44";
import type { AiState } from "./AvatarCanvas";

export interface Rect {
  left: number;
  top: number;
  width: number;
  height: number;
}

export interface Point2D {
  x: number;
  y: number;
}

export const TAP_MOVE_PX = 4;
export const TAP_HOLD_MS = 300;
export const TAP_COOLDOWN_MS = 800;

function clamp(v: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, v));
}

/** Screen point -> NDC (-1..1), Y flipped to match Cubism's up-positive convention. */
export function clientPointToNdc(clientX: number, clientY: number, rect: Rect): Point2D {
  if (rect.width <= 0 || rect.height <= 0) return { x: 0, y: 0 };
  const x = ((clientX - rect.left) / rect.width) * 2 - 1;
  const y = -(((clientY - rect.top) / rect.height) * 2 - 1);
  return { x: clamp(x, -1, 1), y: clamp(y, -1, 1) };
}

/** NDC -> model space, by inverting the exact MVP the most recent draw() call
 * produced (SPEC §20.14.3). Never rebuild the matrix separately from
 * `scale`/`offset_x`/`offset_y` — that creates a second source of truth that
 * silently drifts out of sync with what's actually on screen. */
export function invertMvpPoint(mvp: CubismMatrix44, ndc: Point2D): Point2D {
  return { x: mvp.invertTransformX(ndc.x), y: mvp.invertTransformY(ndc.y) };
}

/** SPEC §20.14.5 — MUST be called with screen (screenX/screenY) deltas, never
 * client coordinates: while dragging the overlay window, the window follows
 * the cursor, so the pointer's position *relative to the window* barely
 * changes, and a real drag would misclassify as a tap. */
export function isTap(dxScreenPx: number, dyScreenPx: number, durationMs: number): boolean {
  return Math.hypot(dxScreenPx, dyScreenPx) < TAP_MOVE_PX && durationMs < TAP_HOLD_MS;
}

/** SPEC §20.14.1 — touch reactions (hover/tap) only fire while the avatar is
 * at rest. Look-follow (§20.14.2) is NOT gated by this and must be checked
 * independently — it keeps running regardless of `aiState`. */
export function canReactToTouch(aiState: AiState): boolean {
  return aiState === "idle";
}

/** SPEC §20.14.4 — a tap within the cooldown of the last *successful*
 * reaction is dropped outright, not queued. A miss (tap that hit neither
 * area) must not consume the cooldown. */
export function canTap(lastReactionAt: number, now: number, cooldownMs: number = TAP_COOLDOWN_MS): boolean {
  return now - lastReactionAt >= cooldownMs;
}

/** Fraction of the model's visible height (from the feet) where the eyes sit.
 * Measured on all three shipped full-body models (elf 90%, highschool 89%,
 * fuwaka ~90%) — see SPEC §20.14.2. Half-body/chibi models may differ. */
export const EYE_HEIGHT_FRACTION = 0.9;

/** The slice of CubismModel needed to find a model's vertical extent. */
export interface DrawableSource {
  getDrawableCount(): number;
  getDrawableVertexCount(i: number): number;
  getDrawableVertices(i: number): Float32Array;
  getDrawableOpacity(i: number): number;
  getDrawableDynamicFlagIsVisible(i: number): boolean;
  getDrawableMasks(): Int32Array[];
  getDrawableMaskCounts(): Int32Array;
}

/** Vertical extent (model space, same space `isHit` uses) of what is actually
 * drawn: visible drawables with non-zero opacity, excluding clipping-mask
 * meshes (they're never rendered and are often much larger than the art). */
export function modelYExtent(src: DrawableSource): { minY: number; maxY: number } | null {
  const count = src.getDrawableCount();
  const masks = src.getDrawableMasks();
  const maskCounts = src.getDrawableMaskCounts();
  const isMask = new Set<number>();
  for (let i = 0; i < count; i++) {
    for (let j = 0; j < maskCounts[i]; j++) isMask.add(masks[i][j]);
  }
  let minY = Infinity;
  let maxY = -Infinity;
  for (let i = 0; i < count; i++) {
    if (isMask.has(i) || !src.getDrawableDynamicFlagIsVisible(i) || src.getDrawableOpacity(i) <= 0) continue;
    const n = src.getDrawableVertexCount(i);
    const v = src.getDrawableVertices(i);
    for (let k = 0; k < n; k++) {
      const y = v[k * 2 + 1];
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
    }
  }
  return minY <= maxY ? { minY, maxY } : null;
}

export function eyeAnchorModelY(ext: { minY: number; maxY: number }, fraction: number = EYE_HEIGHT_FRACTION): number {
  return ext.minY + fraction * (ext.maxY - ext.minY);
}

/** SPEC §20.14.2 — turn the raw pointer NDC into the look-follow target so
 * that a cursor level with the model's eyes means "straight ahead" (0), not a
 * cursor level with the canvas centre. Each side is rescaled separately so
 * the canvas top/bottom still reach full deflection (±1). X is untouched: the
 * eyes measured centred horizontally on every shipped model. */
export function lookTarget(ndc: Point2D, anchorNdcY: number): Point2D {
  const a = clamp(anchorNdcY, -0.9, 0.9);
  const dy = ndc.y - a;
  const y = dy >= 0 ? dy / (1 - a) : dy / (1 + a);
  return { x: ndc.x, y: clamp(y, -1, 1) };
}
