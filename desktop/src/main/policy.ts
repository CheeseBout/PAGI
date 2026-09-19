// Pure decisions of the main process (SPEC §21.3, §21.11, §21.13), kept free
// of the `electron` import so they are unit-testable without an Electron runtime.

export type OverlayMode = "compact" | "bubble" | "panel";

export interface Size {
  width: number;
  height: number;
}

export interface Rect extends Size {
  x: number;
  y: number;
}

// Starting sizes (SPEC §21.3.1). The height includes the 180px bubble zone that
// the /overlay page reserves above the model (BUBBLE_ZONE_PX in pages/Overlay.tsx),
// so a bubble never covers the avatar; that zone is transparent and click-through.
const SIZES: Record<OverlayMode, Size> = {
  compact: { width: 360, height: 640 },
  bubble: { width: 360, height: 640 },
  panel: { width: 780, height: 640 },
};

// ── region capture (SPEC §21.9) ─────────────────────────────────────────
/** A drag smaller than this (CSS px, either side) is treated as a mis-click, not a region. */
export const MIN_CAPTURE_PX = 8;
/** Longest edge kept before encoding: keeps the PNG well under MAX_UPLOAD_MB on a 4K+ display.
 * (The backend re-scales for the model anyway — this only bounds the upload.) */
export const MAX_CAPTURE_EDGE = 3000;

export interface Selection {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** Turn a selection made in a display's CSS-pixel space into a crop rectangle in the
 * captured image's physical pixels. Returns null when there is nothing to crop:
 * a non-finite/absurd payload, a region below MIN_CAPTURE_PX, or fully off-display.
 * The rectangle is clamped to the image so a drag past the screen edge still works. */
export function cropRectFromSelection(
  sel: unknown,
  display: Size,
  image: Size,
): Rect | null {
  if (typeof sel !== "object" || sel === null) return null;
  const s = sel as Record<string, unknown>;
  const nums = [s.x, s.y, s.width, s.height];
  if (!nums.every((n) => typeof n === "number" && Number.isFinite(n))) return null;
  const { x, y, width, height } = s as unknown as Selection;
  if (width < MIN_CAPTURE_PX || height < MIN_CAPTURE_PX) return null;
  if (display.width <= 0 || display.height <= 0) return null;

  const sx = image.width / display.width;
  const sy = image.height / display.height;
  const left = Math.max(0, Math.round(x * sx));
  const top = Math.max(0, Math.round(y * sy));
  const right = Math.min(image.width, Math.round((x + width) * sx));
  const bottom = Math.min(image.height, Math.round((y + height) * sy));
  if (right - left < 1 || bottom - top < 1) return null;
  return { x: left, y: top, width: right - left, height: bottom - top };
}

/** Size after scaling so the longest edge is at most `max` (never upscales). */
export function fitLongestEdge(size: Size, max = MAX_CAPTURE_EDGE): Size {
  const longest = Math.max(size.width, size.height);
  if (longest <= max) return size;
  const k = max / longest;
  return { width: Math.max(1, Math.round(size.width * k)), height: Math.max(1, Math.round(size.height * k)) };
}

export const NOTIFICATION_KINDS = ["approval", "cron", "cron-error", "project", "project-paused"] as const;
export type NotificationKind = (typeof NOTIFICATION_KINDS)[number];

export interface ParsedNotification {
  title: string;
  body: string;
  kind: NotificationKind;
  target: unknown;
  tag: string | null;
}

const TITLE_MAX = 120;
const BODY_MAX = 300;
const TAG_MAX = 120;
const TARGET_MAX_JSON = 600;

/** Validate a `showNotification` payload from the renderer (SPEC §21.11: parameters
 * are checked for type and length). Text is shown as plain text by the OS toast, so
 * no escaping is needed, but nothing unbounded gets through. Null = reject. */
export function parseNotification(raw: unknown): ParsedNotification | null {
  if (typeof raw !== "object" || raw === null) return null;
  const r = raw as Record<string, unknown>;
  if (typeof r.title !== "string" || typeof r.body !== "string") return null;
  if (!(NOTIFICATION_KINDS as readonly string[]).includes(r.kind as string)) return null;
  if (r.tag !== undefined && (typeof r.tag !== "string" || r.tag.length > TAG_MAX)) return null;
  let targetJson: string;
  try {
    targetJson = JSON.stringify(r.target ?? null);
  } catch {
    return null;
  }
  if (targetJson === undefined || targetJson.length > TARGET_MAX_JSON) return null;
  const title = r.title.trim().slice(0, TITLE_MAX);
  if (!title) return null;
  return {
    title,
    body: r.body.trim().slice(0, BODY_MAX),
    kind: r.kind as NotificationKind,
    target: JSON.parse(targetJson),
    tag: (r.tag as string | undefined) ?? null,
  };
}

export function isOverlayMode(v: unknown): v is OverlayMode {
  return v === "compact" || v === "bubble" || v === "panel";
}

export function sizeForMode(mode: OverlayMode): Size {
  return SIZES[mode];
}

/** Origin of a URL, or null if it isn't a valid absolute URL. */
export function originOf(url: string): string | null {
  try {
    return new URL(url).origin;
  } catch {
    return null;
  }
}

/** IPC sender check (SPEC §21.11): only frames of the overlay origin may call in. */
export function isTrustedSender(senderUrl: string | undefined, allowedOrigin: string): boolean {
  if (!senderUrl) return false;
  return originOf(senderUrl) === allowedOrigin;
}

/** In-window navigation is locked to the overlay origin (SPEC §21.13). */
export function isAllowedNavigation(url: string, allowedOrigin: string): boolean {
  return originOf(url) === allowedOrigin;
}

/** `openExternal` accepts only http(s) URLs of the overlay origin (SPEC §21.11). */
export function isAllowedExternalUrl(url: string, allowedOrigin: string): boolean {
  let u: URL;
  try {
    u = new URL(url);
  } catch {
    return false;
  }
  return (u.protocol === "http:" || u.protocol === "https:") && u.origin === allowedOrigin;
}

const LOOPBACK = new Set(["localhost", "127.0.0.1", "[::1]", "::1"]);

/** SPEC §21.13: warn when the cookie would travel over plain HTTP off-box. */
export function isInsecureRemote(url: string): boolean {
  try {
    const u = new URL(url);
    return u.protocol === "http:" && !LOOPBACK.has(u.hostname);
  } catch {
    return false;
  }
}

/** Keep a window fully inside a work area (SPEC §21.3: always within a display). */
export function clampToArea(rect: Rect, area: Rect): Rect {
  const width = Math.min(rect.width, area.width);
  const height = Math.min(rect.height, area.height);
  const x = Math.min(Math.max(rect.x, area.x), area.x + area.width - width);
  const y = Math.min(Math.max(rect.y, area.y), area.y + area.height - height);
  return { x, y, width, height };
}

export function rectInside(rect: Rect, area: Rect): boolean {
  return (
    rect.x >= area.x &&
    rect.y >= area.y &&
    rect.x + rect.width <= area.x + area.width &&
    rect.y + rect.height <= area.y + area.height
  );
}

/** Default placement: bottom-right corner of the primary work area. */
export function defaultRect(size: Size, area: Rect, margin = 24): Rect {
  return {
    ...size,
    x: area.x + area.width - size.width - margin,
    y: area.y + area.height - size.height - margin,
  };
}

/** Where to restore the window: saved spot if it is still on a display, else default. */
export function restoreRect(saved: Rect | null, size: Size, areas: Rect[], primary: Rect): Rect {
  if (saved) {
    const candidate = { ...saved, ...size };
    if (areas.some((a) => rectInside(candidate, a))) return candidate;
  }
  return defaultRect(size, primary);
}
