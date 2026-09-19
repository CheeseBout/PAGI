// Connects the overlay to the global notification stream (SPEC §21.7) and turns
// events into native toasts through the desktop shell (SPEC §21.8, §21.10).
// The renderer owns the socket (it has the login cookie; the main process does
// not) and stays alive while the window is hidden, which is what lets a hidden
// overlay still raise a toast.
import { useEffect, useRef } from "react";
import { NotificationSocket } from "@/api/ws";
import { getDesktop, type NotificationClick } from "@/lib/pagiDesktop";
import { toastFor, type ToastTarget } from "./toasts";

export interface OverlayNotificationOptions {
  /** only connect while logged in */
  enabled: boolean;
  /** the overlay's own continuous session, if any */
  sessionId: string | null;
  onAuthLost: () => void;
  /** called on every (re)connect — events aren't replayed, so re-sync from REST */
  onResync: () => void;
  /** the user clicked a toast */
  onClick: (target: ToastTarget) => void;
}

export function useOverlayNotifications(opts: OverlayNotificationOptions) {
  const ref = useRef(opts);
  ref.current = opts;
  // Whether the overlay is on screen. Reported by the shell: document.hidden stays
  // false after the window is hidden, so it can't be used (SPEC §21.8). A plain
  // browser tab has no shell, so it falls back to the page's own visibility.
  const shellVisible = useRef(true);

  useEffect(() => {
    if (!opts.enabled) return;
    const desktop = getDesktop();

    const socket = new NotificationSocket(
      (event) => {
        if (!desktop) return; // a plain browser tab has no native toasts
        if (event.type === "approval_resolved") {
          desktop.closeNotification(event.approval_id); // decided elsewhere: drop its toast
          return;
        }
        const toast = toastFor(event, {
          windowVisible: desktop ? shellVisible.current : !document.hidden,
          overlaySessionId: ref.current.sessionId,
        });
        if (toast) {
          desktop.showNotification({
            title: toast.title,
            body: toast.body,
            kind: toast.kind,
            target: toast.target,
            tag: toast.approvalId,
          });
        }
      },
      () => ref.current.onResync(),
      () => ref.current.onAuthLost(),
    );
    socket.connect();

    const unsubVisibility = desktop?.onVisibilityChange((v) => {
      shellVisible.current = v;
    });

    const unsubscribe = desktop?.onNotificationClick((click: NotificationClick) => {
      const t = click.target as ToastTarget | null;
      if (t && (t.type === "overlay-approval" || t.type === "web")) ref.current.onClick(t);
    });

    return () => {
      socket.close();
      unsubscribe?.();
      unsubVisibility?.();
    };
    // one socket per login; the latest callbacks/session are read through `ref`
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opts.enabled]);
}
