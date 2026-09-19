// Avatar state machine (SPEC §20.6) — derives `AiState` purely from the same
// `ServerEvent` stream `ChatWindow` already consumes (see useChatSession's
// `latestEvent`), plus a pseudo-event fired right when the user sends. The
// pure transition table lives in avatarStateReducer.ts; this hook only owns
// the IDLE_TIMEOUT_MS safety-net timer (a real side effect).
import { useEffect, useRef, useState } from "react";
import type { ServerEvent } from "@/api/ws";
import {
  initialAvatarReducerState,
  nextOnEvent,
  nextOnIdleTimeout,
  nextOnSendPulse,
  type AvatarReducerState,
  type TimerAction,
} from "./avatarStateReducer";

// Proposed in SPEC §20.13 as a starting point, not a final constant — tune by
// observation once the avatar is actually being watched.
const IDLE_TIMEOUT_MS = 4000;

export function useAvatarState(latestEvent: ServerEvent | null, didSendMessage: boolean) {
  const [state, setState] = useState<AvatarReducerState>(initialAvatarReducerState);
  const stateRef = useRef(state);
  stateRef.current = state;
  const idleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const prevPulse = useRef(didSendMessage);

  function applyTimer(action: TimerAction) {
    if (idleTimer.current) {
      clearTimeout(idleTimer.current);
      idleTimer.current = null;
    }
    if (action === "arm") {
      idleTimer.current = setTimeout(() => setState((s) => nextOnIdleTimeout(s)), IDLE_TIMEOUT_MS);
    }
  }

  // pseudo-event: sendUserMessage / regenerate / editMessage was just called.
  // `didSendMessage` is a toggled signal, not a literal on/off state — any
  // change (either direction) means "a send just happened".
  useEffect(() => {
    if (didSendMessage === prevPulse.current) return;
    prevPulse.current = didSendMessage;
    const { state: next, timer } = nextOnSendPulse(stateRef.current);
    setState(next);
    applyTimer(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [didSendMessage]);

  useEffect(() => {
    if (!latestEvent) return;
    const { state: next, timer } = nextOnEvent(stateRef.current, latestEvent);
    setState(next);
    applyTimer(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [latestEvent]);

  useEffect(
    () => () => {
      if (idleTimer.current) clearTimeout(idleTimer.current);
    },
    [],
  );

  return state.aiState;
}
