// Turns the live chat state into "what the bubble shows right now", plus the
// timers that let a finished reply / an error fade out (SPEC §21.4.2).
import { useCallback, useEffect, useState } from "react";
import type { ChatLiveState } from "@/features/chat/chatLiveReducer";
import { BUBBLE_ERROR_MS, BUBBLE_TTL_MS, pickBubble, type Bubble } from "./bubbleRules";

export function useBubble(live: ChatLiveState, hovering: boolean): { bubble: Bubble | null; dismiss: () => void } {
  const [doneText, setDoneText] = useState<string | null>(null);
  const [errorVisible, setErrorVisible] = useState(false);

  // a turn just finished: its reply becomes the (temporary) bubble
  useEffect(() => {
    if (live.lastMessageDone) setDoneText(live.lastMessageDone.content || null);
  }, [live.lastMessageDone]);

  // ...and fades after the TTL; hovering pauses the countdown, and a new
  // stream replaces it (streaming text has priority in pickBubble anyway)
  useEffect(() => {
    if (!doneText || hovering || live.streamingText) return;
    const t = setTimeout(() => setDoneText(null), BUBBLE_TTL_MS);
    return () => clearTimeout(t);
  }, [doneText, hovering, live.streamingText]);

  useEffect(() => {
    setErrorVisible(live.error !== null);
  }, [live.error]);

  useEffect(() => {
    if (!errorVisible || hovering) return;
    const t = setTimeout(() => setErrorVisible(false), BUBBLE_ERROR_MS);
    return () => clearTimeout(t);
  }, [errorVisible, hovering]);

  const runningTool = live.toolEvents.find((t) => t.status === "running")?.tool_name ?? null;
  const bubble = pickBubble({
    approvals: live.approvals,
    error: live.error,
    errorVisible,
    streamingText: live.streamingText,
    runningTool,
    doneText,
  });

  const dismiss = useCallback(() => {
    setDoneText(null);
    setErrorVisible(false);
  }, []);

  return { bubble, dismiss };
}
