// The overlay's view of its one continuous conversation (SPEC §21.4, §21.10):
// reuses useChatSession for the socket/reducer, adds the message list, the
// "turn in progress" flag, and connection health (offline banner / auth lost).
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, type ChatMessage } from "@/api/client";
import { useChatSession } from "@/features/chat/useChatSession";

export type Connection = "connecting" | "online" | "offline";

// grace before declaring the socket "down" — brief reconnects shouldn't flash a banner
const OFFLINE_GRACE_MS = 2000;

export function useOverlayChat(sessionId: string | null, onAuthLost: () => void) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [busy, setBusy] = useState(false);
  const [connection, setConnection] = useState<Connection>("connecting");
  const sessionRef = useRef(sessionId);
  sessionRef.current = sessionId;

  const reload = useCallback(async (id: string) => {
    try {
      const data = await api.getConversation(id);
      if (sessionRef.current === id) setMessages(data.messages);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) onAuthLost();
      /* otherwise keep what we have; the connection banner covers "backend down" */
    }
  }, [onAuthLost]);

  const chat = useChatSession(
    sessionId,
    {
      onMessageDone: () => {
        setBusy(false);
        if (sessionRef.current) void reload(sessionRef.current);
      },
      onApprovalResolved: () => {},
      onFileToolTouched: () => {},
    },
    onAuthLost,
  );
  const { seedApprovals, wsOpen, sendUserMessage, abort: chatAbort } = chat;

  const resync = useCallback(
    (id: string) => {
      void reload(id);
      api
        .listPendingApprovals()
        .then((all) => {
          if (sessionRef.current === id) seedApprovals(all.filter((a) => a.session_id === id));
        })
        .catch(() => {});
    },
    [reload, seedApprovals],
  );

  // session changed: fresh list + any approvals still pending for it
  useEffect(() => {
    setMessages([]);
    setBusy(false);
    if (sessionId) resync(sessionId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  // an error or abort ends the turn too
  useEffect(() => {
    if (chat.live.error) setBusy(false);
  }, [chat.live.error]);

  // Connection health. The server rejects an unauthenticated WS handshake with a
  // plain HTTP 403 (no 4401 frame reaches the browser), so a socket that stays
  // closed is checked against /auth/me to tell "logged out" from "backend down".
  const wasOpen = useRef(false);
  const messagesLoadedOnce = useRef(false);
  useEffect(() => {
    messagesLoadedOnce.current = false;
  }, [sessionId]);
  useEffect(() => {
    if (!sessionId) return;
    if (wsOpen) {
      setConnection("online");
      // reconnect (not first connect): re-sync what we may have missed (SPEC §21.10)
      if (wasOpen.current === false && messagesLoadedOnce.current) resync(sessionId);
      wasOpen.current = true;
      messagesLoadedOnce.current = true;
      return;
    }
    wasOpen.current = false;
    let live = true;
    const t = setTimeout(() => {
      api
        .me()
        .then(() => live && setConnection("connecting"))
        .catch((e) => {
          if (!live) return;
          if (e instanceof ApiError && e.status === 401) onAuthLost();
          else setConnection("offline");
        });
    }, OFFLINE_GRACE_MS);
    return () => {
      live = false;
      clearTimeout(t);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wsOpen, sessionId]);
  const send = useCallback(
    (content: string, attachmentIds: string[] = []) => {
      const text = content.trim();
      // a screenshot alone is a valid message (the server accepts empty text with attachments)
      if ((!text && attachmentIds.length === 0) || !sessionRef.current) return false;
      setBusy(true);
      setMessages((prev) => [
        ...prev,
        {
          id: `local-${Date.now()}`,
          role: "user",
          content: text,
          created_at: new Date().toISOString(),
        },
      ]);
      sendUserMessage(text, attachmentIds);
      return true;
    },
    [sendUserMessage],
  );

  const abort = useCallback(() => {
    chatAbort();
    setBusy(false);
  }, [chatAbort]);

  // re-read what the server holds for the current session (messages + pending approvals)
  const resyncCurrent = useCallback(() => {
    if (sessionRef.current) resync(sessionRef.current);
  }, [resync]);

  return { ...chat, messages, busy, connection, send, abort, resyncCurrent };
}
