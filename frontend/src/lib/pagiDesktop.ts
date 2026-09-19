// Bridge exposed by the Electron preload (desktop/src/preload, SPEC §21.11).
// Undefined in a normal browser tab, so every call site must tolerate that —
// the /overlay route is still openable in a browser for development.
export type OverlayMode = "compact" | "bubble" | "panel";

export interface DesktopNotification {
  title: string;
  body: string;
  kind: string;
  /** opaque to the shell: relayed back verbatim through onNotificationClick */
  target: unknown;
  /** lets closeNotification remove this toast later (e.g. approval id) */
  tag?: string;
}

export interface NotificationClick {
  kind: string;
  target: unknown;
}

export interface PagiDesktop {
  setMode(mode: OverlayMode): void;
  setInteractive(interactive: boolean): void;
  dragStart(): void;
  dragMove(dx: number, dy: number): void;
  dragEnd(): void;
  /** open an http(s) URL of the overlay origin in the default browser (SPEC §21.11) */
  openExternal(url: string): void;
  /** native OS toast (SPEC §21.8); clicking it shows the overlay and fires onNotificationClick */
  showNotification(n: DesktopNotification): void;
  closeNotification(tag: string): void;
  /** Region screenshot (SPEC §21.9): resolves with PNG bytes, or null if the user cancelled.
   * Only ever call this from a user gesture (button, hotkey, /screen). */
  startCapture(): Promise<ArrayBuffer | null>;
  /** the capture hotkey was pressed; returns an unsubscribe function */
  onCaptureHotkey(cb: () => void): () => void;
  /** the shell shows/hides the window (hotkey, tray); document.hidden does NOT reflect this */
  onVisibilityChange(cb: (visible: boolean) => void): () => void;
  /** returns an unsubscribe function */
  onNotificationClick(cb: (click: NotificationClick) => void): () => void;
  getInfo(): Promise<{ version: string; platform: string }>;
}

declare global {
  interface Window {
    pagiDesktop?: PagiDesktop;
  }
}

export function getDesktop(): PagiDesktop | undefined {
  return typeof window === "undefined" ? undefined : window.pagiDesktop;
}
