// Pure rules for what the overlay bubble shows (SPEC §21.4.2) and which window
// mode that implies (§21.3.1). No React here so the rules are unit-testable.
import type { Approval } from "@/api/client";
import type { OverlayMode } from "@/lib/pagiDesktop";

// Starting values, meant to be tuned by use (SPEC §21.16).
export const BUBBLE_MAX_CHARS = 280;
export const BUBBLE_TTL_MS = 8000;
export const BUBBLE_ERROR_MS = 6000;

const FENCE = "```";
// a markdown table = a header row followed by a |---|---| separator row
const TABLE_SEP = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$/m;

/** Code fences and tables don't read well in a small bubble (SPEC §21.4.2). */
export function hasBlockContent(text: string): boolean {
  return text.includes(FENCE) || TABLE_SEP.test(text);
}

export interface BubbleText {
  text: string;
  /** more exists than is shown — the bubble adds a "click to view" hint */
  truncated: boolean;
}

function cutAtWord(text: string, max: number): string {
  if (text.length <= max) return text;
  const head = text.slice(0, max);
  const lastSpace = head.lastIndexOf(" ");
  return (lastSpace > max * 0.6 ? head.slice(0, lastSpace) : head).trimEnd();
}

/** Short plain replies are shown whole; anything longer, or with a code block or
 * table, shows only its beginning. It never opens the panel by itself. */
export function toBubbleText(raw: string, max = BUBBLE_MAX_CHARS): BubbleText {
  const text = raw.trim();
  if (!hasBlockContent(text) && text.length <= max) return { text, truncated: false };

  let head = text;
  const fenceAt = head.indexOf(FENCE);
  if (fenceAt !== -1) head = head.slice(0, fenceAt);
  const sep = TABLE_SEP.exec(head);
  if (sep) {
    // drop the table, including its header row (the line just above the separator)
    const before = head.slice(0, sep.index).replace(/[^\n]*\n?$/, "");
    head = before;
  }
  head = cutAtWord(head.trim(), max);
  return { text: head, truncated: true };
}

export type Bubble =
  | { kind: "approval"; approval: Approval }
  | { kind: "error"; message: string }
  | { kind: "text"; text: BubbleText; streaming: boolean }
  | { kind: "tool"; label: string };

export interface BubbleInput {
  approvals: Approval[];
  error: string | null;
  errorVisible: boolean;
  streamingText: string | null;
  /** name of a tool call that is currently running, if any */
  runningTool: string | null;
  /** the finished reply of the latest turn, while its bubble is still alive */
  doneText: string | null;
}

export function toolLabel(name: string): string {
  return `Đang dùng ${name}…`;
}

/** Priority (SPEC §21.4.2): approval card > error > streaming text > tool label
 * > finished reply. An approval card is never displaced by anything else. */
export function pickBubble(i: BubbleInput): Bubble | null {
  if (i.approvals.length > 0) return { kind: "approval", approval: i.approvals[0] };
  if (i.error && i.errorVisible) return { kind: "error", message: i.error };
  if (i.streamingText) return { kind: "text", text: toBubbleText(i.streamingText), streaming: true };
  if (i.runningTool) return { kind: "tool", label: toolLabel(i.runningTool) };
  if (i.doneText) return { kind: "text", text: toBubbleText(i.doneText), streaming: false };
  return null;
}

/** Window mode implied by the UI state (SPEC §21.3.1). The panel wins; an open
 * panel already shows the conversation, so only the bubble-less `compact` and
 * `panel` modes exist while it is open. */
export function overlayMode(panelOpen: boolean, bubble: Bubble | null): OverlayMode {
  if (panelOpen) return "panel";
  return bubble ? "bubble" : "compact";
}
