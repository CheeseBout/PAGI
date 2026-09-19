// Frame-rate policy for the overlay's avatar (SPEC §21.12): full rate (the 30fps
// cap of §20.9) while anything is happening, half rate once the overlay has been
// idle and untouched for IDLE_SLOWDOWN_MS. Pure, so it is unit-testable; the hook
// below wires it to real input.
import { useEffect, useRef, useState } from "react";

export const ACTIVE_FPS = 30;
export const IDLE_FPS = 15;
export const IDLE_SLOWDOWN_MS = 30_000;

export interface IdleInput {
  /** avatar is busy (thinking/talking/acting/waiting) or a bubble/panel is up */
  busy: boolean;
  /** ms since the last pointer/keyboard input */
  sinceInputMs: number;
  /** threshold; defaults to IDLE_SLOWDOWN_MS */
  idleMs?: number;
}

export function fpsFor({ busy, sinceInputMs, idleMs = IDLE_SLOWDOWN_MS }: IdleInput): number {
  if (busy) return ACTIVE_FPS;
  return sinceInputMs >= idleMs ? IDLE_FPS : ACTIVE_FPS;
}

/** Dev/test knob: `/overlay?idleMs=2000` shortens the threshold. Ignored if invalid. */
export function idleMsFromSearch(search: string): number {
  const raw = new URLSearchParams(search).get("idleMs");
  const n = raw === null ? NaN : Number(raw);
  return Number.isFinite(n) && n >= 500 ? n : IDLE_SLOWDOWN_MS;
}

const CHECK_MS = 500;

/** Current frame cap for the avatar. Any pointer/keyboard input, or `busy`, restores full rate at once. */
export function useIdleFps(busy: boolean): number {
  const [fps, setFps] = useState(ACTIVE_FPS);
  const lastInput = useRef(Date.now());
  const busyRef = useRef(busy);
  busyRef.current = busy;
  const idleMs = useRef(idleMsFromSearch(typeof window === "undefined" ? "" : window.location.search));

  useEffect(() => {
    const evaluate = () => {
      const next = fpsFor({ busy: busyRef.current, sinceInputMs: Date.now() - lastInput.current, idleMs: idleMs.current });
      setFps((cur) => (cur === next ? cur : next));
    };
    const onInput = () => {
      lastInput.current = Date.now();
      evaluate(); // wake up immediately, don't wait for the next tick
    };
    const events = ["mousemove", "mousedown", "keydown", "wheel"] as const;
    for (const e of events) window.addEventListener(e, onInput, { passive: true });
    const t = setInterval(evaluate, CHECK_MS);
    return () => {
      for (const e of events) window.removeEventListener(e, onInput);
      clearInterval(t);
    };
  }, []);

  // becoming busy (a reply starts, an approval appears) must not wait for the next tick
  useEffect(() => {
    if (busy) setFps(ACTIVE_FPS);
  }, [busy]);

  return fps;
}
