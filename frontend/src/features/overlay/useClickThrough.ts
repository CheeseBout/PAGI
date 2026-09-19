// Wires mouse movement to the desktop shell's click-through switch
// (SPEC §21.3.2) and implements window dragging over the model (IPC-based —
// `-webkit-app-region: drag` swallows mouse events and would defeat the
// hit-testing below).
import { useEffect, useRef, type RefObject } from "react";
import { getDesktop } from "@/lib/pagiDesktop";
import { computeInteractive, MODEL_ATTR } from "./hitTest";

export function useClickThrough(rootRef: RefObject<HTMLElement | null>) {
  const interactiveRef = useRef<boolean | null>(null);
  const dragging = useRef<{ x: number; y: number } | null>(null);
  const focusedInput = useRef(false);
  // latest pending drag delta, flushed at most once per frame: mousemove can
  // fire far faster than the window can repaint, and every IPC call moves it.
  const pendingDrag = useRef<{ dx: number; dy: number } | null>(null);
  const dragRaf = useRef(0);

  useEffect(() => {
    const desktop = getDesktop();
    if (!desktop) return; // plain browser tab: nothing to switch

    const apply = (next: boolean) => {
      // only cross the IPC boundary when the value changes (SPEC §21.3.2)
      if (interactiveRef.current === next) return;
      interactiveRef.current = next;
      desktop.setInteractive(next);
    };

    const forced = () => dragging.current !== null || focusedInput.current;

    const onMove = (e: MouseEvent) => {
      if (dragging.current) {
        pendingDrag.current = { dx: e.screenX - dragging.current.x, dy: e.screenY - dragging.current.y };
        if (!dragRaf.current) {
          dragRaf.current = requestAnimationFrame(() => {
            dragRaf.current = 0;
            const d = pendingDrag.current;
            pendingDrag.current = null;
            if (d && dragging.current) desktop.dragMove(d.dx, d.dy);
          });
        }
        return;
      }
      const root = rootRef.current;
      const modelEl = root?.querySelector<HTMLElement>(`[${MODEL_ATTR}]`);
      const r = modelEl?.getBoundingClientRect();
      apply(
        computeInteractive({
          point: { x: e.clientX, y: e.clientY },
          target: document.elementFromPoint(e.clientX, e.clientY),
          modelBox: r ? { left: r.left, top: r.top, right: r.right, bottom: r.bottom } : null,
          forced: forced(),
        }),
      );
    };

    const onDown = (e: MouseEvent) => {
      const el = e.target as Element | null;
      if (!el?.closest(`[${MODEL_ATTR}]`)) return;
      dragging.current = { x: e.screenX, y: e.screenY };
      apply(true);
      desktop.dragStart();
    };

    const onUp = () => {
      if (!dragging.current) return;
      dragging.current = null;
      pendingDrag.current = null;
      desktop.dragEnd();
    };

    const onFocusIn = (e: FocusEvent) => {
      const el = e.target as HTMLElement | null;
      if (el && /^(INPUT|TEXTAREA)$/.test(el.tagName)) {
        focusedInput.current = true;
        apply(true);
      }
    };
    const onFocusOut = () => {
      focusedInput.current = false;
    };

    window.addEventListener("mousemove", onMove);
    window.addEventListener("mousedown", onDown);
    window.addEventListener("mouseup", onUp);
    window.addEventListener("focusin", onFocusIn);
    window.addEventListener("focusout", onFocusOut);
    apply(false);
    return () => {
      if (dragRaf.current) cancelAnimationFrame(dragRaf.current);
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("mouseup", onUp);
      window.removeEventListener("focusin", onFocusIn);
      window.removeEventListener("focusout", onFocusOut);
    };
  }, [rootRef]);
}
