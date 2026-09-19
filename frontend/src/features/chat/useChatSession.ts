// Owns the live/ephemeral side of one conversation: the WebSocket connection
// and the reducer it drives. `messages`/conversations/grants stay in the page
// (pagination, edit, regenerate all touch `messages` too) — this hook notifies
// the page via callbacks instead of owning that state itself.
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import type { Approval } from "@/api/client";
import { ChatSocket, type ServerEvent } from "@/api/ws";
import {
  chatLiveReducer,
  initialChatLiveState,
  type ChatLiveAction,
  type ChatLiveState,
  type MessageDoneInfo,
} from "./chatLiveReducer";

const FILE_TOOLS = new Set(["write_file", "edit_file", "delete_file", "execute_code"]);

export interface ChatSessionCallbacks {
  /** A turn finished. The page decides what to do with it — appending it
   * optimistically is only safe when `hadToolCalls` is false (see Chat.tsx). */
  onMessageDone: (info: MessageDoneInfo) => void;
  /** An approval (this session's or a sub-agent's) was approved/denied — the
   * page re-fetches `session_tool_grants` since a "remember for this chat"
   * decision may have added one. */
  onApprovalResolved: () => void;
  /** A file tool (write/edit/delete/execute_code) finished — the page bumps
   * the workspace panel's refresh key. */
  onFileToolTouched: () => void;
}

export function useChatSession(
  currentId: string | null,
  callbacks: ChatSessionCallbacks,
  /** the socket was closed with 4401 (not logged in) — see ChatSocket */
  onAuthLost?: () => void,
) {
  const reducer = useCallback(
    (state: ChatLiveState, action: ChatLiveAction) => chatLiveReducer(state, action, { currentId }),
    [currentId],
  );
  const [live, dispatch] = useReducer(reducer, initialChatLiveState);

  // Mirrors let handleEvent (registered once per socket connection) always see
  // the latest toolEvents/callbacks without needing to reconnect the socket.
  const toolEventsRef = useRef(live.toolEvents);
  useEffect(() => {
    toolEventsRef.current = live.toolEvents;
  }, [live.toolEvents]);
  const callbacksRef = useRef(callbacks);
  useEffect(() => {
    callbacksRef.current = callbacks;
  });
  const onAuthLostRef = useRef(onAuthLost);
  useEffect(() => {
    onAuthLostRef.current = onAuthLost;
  });

  const [wsOpen, setWsOpen] = useState(false);
  const socketRef = useRef<ChatSocket | null>(null);

  // Raw event stream + send-pulse, exposed for useAvatarState (SPEC §20.6) —
  // avatar state is derived independently of the reducer above, so a change
  // here never affects the reducer's own behaviour.
  const [latestEvent, setLatestEvent] = useState<ServerEvent | null>(null);
  const [sendPulse, setSendPulse] = useState(false);

  const handleEvent = useCallback((event: ServerEvent) => {
    // Side effects that need to inspect pre-dispatch state (or need no state
    // at all) are decided here, before the pure reducer runs.
    if (event.type === "tool_call_result") {
      const hit = toolEventsRef.current.find((t) => t.tool_call_id === event.tool_call_id);
      if (hit && FILE_TOOLS.has(hit.tool_name)) callbacksRef.current.onFileToolTouched();
    } else if (event.type === "approval_resolved") {
      callbacksRef.current.onApprovalResolved();
    }
    setLatestEvent(event);
    dispatch(event);
  }, []);

  // message_done's payload comes from the reducer itself (it already knows
  // the accumulated text and whether tools ran) — just forward it.
  useEffect(() => {
    if (live.lastMessageDone) callbacksRef.current.onMessageDone(live.lastMessageDone);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live.lastMessageDone]);

  useEffect(() => {
    dispatch({ type: "reset" });
    setLatestEvent(null);
    socketRef.current?.close();
    socketRef.current = null;
    if (!currentId) return;
    const sock = new ChatSocket(
      currentId,
      handleEvent,
      (s) => setWsOpen(s === "open"),
      () => onAuthLostRef.current?.(),
    );
    sock.connect();
    socketRef.current = sock;
    return () => sock.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentId]);

  // Every action below only closes over `dispatch` (stable, per useReducer)
  // and `socketRef.current` (a ref, read fresh on call) — so each can have an
  // empty dep array and stay referentially stable across renders. That matters
  // because `live` changes on every WS event, and without stable callbacks
  // here, anything memoized downstream (e.g. MessageBubble) would re-render
  // just as often as if it weren't memoized at all.
  const seedApprovals = useCallback(
    (approvals: Approval[]) => dispatch({ type: "seed_approvals", approvals }),
    [],
  );
  const reportError = useCallback((message: string) => dispatch({ type: "external_error", message }), []);
  const sendUserMessage = useCallback((content: string, attachmentIds: string[] = []) => {
    dispatch({ type: "reset_for_new_turn" });
    setSendPulse((p) => !p);
    socketRef.current?.sendUserMessage(content, attachmentIds);
  }, []);
  const abort = useCallback(() => {
    socketRef.current?.abort();
    dispatch({ type: "clear_streaming_text" });
  }, []);
  const regenerate = useCallback(() => {
    dispatch({ type: "reset_for_edit_or_regenerate" });
    setSendPulse((p) => !p);
    socketRef.current?.regenerate();
  }, []);
  const editMessage = useCallback((messageId: string, content: string) => {
    dispatch({ type: "reset_for_edit_or_regenerate" });
    setSendPulse((p) => !p);
    socketRef.current?.editMessage(messageId, content);
  }, []);
  const decideApproval = useCallback(
    (approvalId: string, decision: "approve" | "deny", remember?: "session") => {
      socketRef.current?.decideApproval(approvalId, decision, remember);
      dispatch({ type: "remove_approval_locally", approval_id: approvalId });
    },
    [],
  );

  return {
    live,
    wsOpen,
    seedApprovals,
    reportError,
    sendUserMessage,
    abort,
    regenerate,
    editMessage,
    decideApproval,
    latestEvent,
    sendPulse,
  };
}
