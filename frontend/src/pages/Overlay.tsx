// Desktop overlay page (SPEC §21.4): the avatar, an always-visible input row,
// a speech bubble for short replies and an expandable panel for the rest, all
// on one continuous chat session. Runs in the Electron shell's transparent
// window (desktop/), but is a normal route — openable in a browser tab for dev,
// where the desktop-only calls (window mode, click-through) are simply absent.
import { useCallback, useEffect, useRef, useState } from "react";
import AvatarCanvas, { type AvatarConfig } from "../components/Avatar/AvatarCanvas";
import { api } from "../api/client";
import OverlayBubble from "../components/overlay/OverlayBubble";
import OverlayInput from "../components/overlay/OverlayInput";
import OverlayLogin from "../components/overlay/OverlayLogin";
import OverlayPanel from "../components/overlay/OverlayPanel";
import { overlayMode } from "../features/overlay/bubbleRules";
import { HIT_ATTR, MODEL_ATTR } from "../features/overlay/hitTest";
import { useBubble } from "../features/overlay/useBubble";
import { useIdleFps } from "../features/overlay/idleSlowdown";
import { useClickThrough } from "../features/overlay/useClickThrough";
import { parseScreenCommand } from "../features/overlay/screenCommand";
import { useScreenCapture } from "../features/overlay/useScreenCapture";
import { useOverlayChat } from "../features/overlay/useOverlayChat";
import { useOverlayNotifications } from "../features/overlay/useOverlayNotifications";
import { useOverlaySession } from "../features/overlay/useOverlaySession";
import { useAvatarState } from "../hooks/useAvatarState";
import { getDesktop } from "@/lib/pagiDesktop";

// spread onto JSX so the data-* marker names live in one place (hitTest.ts)
const hit = { [HIT_ATTR]: "" };
const model = { [MODEL_ATTR]: "" };

// Height of the bubble zone above the model. The desktop window heights in
// desktop/src/main/policy.ts include it — keep the two in step.
const BUBBLE_ZONE_PX = 180;

export default function Overlay() {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [hovering, setHovering] = useState(false);
  const [draft, setDraft] = useState("");
  const [uploading, setUploading] = useState(false);

  useClickThrough(rootRef);

  // the shared stylesheet paints an opaque page background; the overlay
  // window must see through it (`html.overlay` rule in styles.css).
  useEffect(() => {
    document.documentElement.classList.add("overlay");
    return () => document.documentElement.classList.remove("overlay");
  }, []);

  const { auth, setAuth, goLoggedOut, agents, agent, sessionId, note, selectAgent } = useOverlaySession();
  const chat = useOverlayChat(sessionId, goLoggedOut);
  const { bubble: rawBubble, dismiss } = useBubble(chat.live, hovering);
  const aiState = useAvatarState(chat.latestEvent, chat.sendPulse);

  // An open panel already shows the conversation, so it only lets the two
  // things that need attention through: an approval card and an error.
  const bubble =
    panelOpen && rawBubble && rawBubble.kind !== "approval" && rawBubble.kind !== "error" ? null : rawBubble;

  // Half frame rate once idle and untouched for a while (SPEC §21.12); any activity restores it.
  const avatarFps = useIdleFps(aiState !== "idle" || bubble !== null || panelOpen);

  // A bubble removed from under the cursor (e.g. right after clicking Duyệt) never
  // fires mouseleave, which would leave `hovering` stuck on and freeze the fade-out
  // of every later bubble. No bubble => nothing can be hovered.
  const hasBubble = bubble !== null;
  useEffect(() => {
    if (!hasBubble) setHovering(false);
  }, [hasBubble]);

  // window size follows the UI state (SPEC §21.3.1) — the shell does the resize
  const mode = overlayMode(panelOpen, bubble);
  useEffect(() => {
    getDesktop()?.setMode(mode);
  }, [mode]);

  useEffect(() => {
    if (!panelOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setPanelOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [panelOpen]);

  // Vision is on unless the agent says otherwise (screenshots need it, SPEC §21.9)
  const visionOk = agent?.vision_enabled !== false;
  const capture = useScreenCapture(visionOk);

  // Enter / Send button. `/screen [question]` starts a capture instead of sending;
  // otherwise text and/or the pending screenshot go out as one message. The
  // screenshot is uploaded only here, i.e. only once the user chose to send it.
  const submit = useCallback(async () => {
    if (chat.busy || uploading || !sessionId) return;
    const cmd = parseScreenCommand(draft);
    if (cmd.isCommand) {
      setDraft(cmd.rest);
      await capture.begin();
      return;
    }
    const text = draft.trim();
    const image = capture.image;
    if (!text && !image) return;
    dismiss();
    if (!image) {
      if (chat.send(text)) setDraft("");
      return;
    }
    setUploading(true);
    try {
      const file = new File([image.blob], "screenshot.png", { type: "image/png" });
      const attachment = await api.uploadFile(sessionId, file);
      if (chat.send(text, [attachment.id])) {
        setDraft("");
        capture.clear();
      }
    } catch (e) {
      chat.reportError(e instanceof Error ? e.message : "Không gửi được ảnh");
    } finally {
      setUploading(false);
    }
  }, [chat, dismiss, draft, capture, sessionId, uploading]);

  // open a page of the web UI in the default browser (the shell only allows our own origin)
  const openWeb = useCallback((path: string) => {
    const url = `${window.location.origin}${path}`;
    const desktop = getDesktop();
    if (desktop) desktop.openExternal(url);
    else window.open(url, "_blank");
  }, []);

  const openHistory = useCallback(() => {
    if (!sessionId) return;
    // the web Chat page opens the conversation named by ?session= (SPEC §21.4.3)
    openWeb(`/?session=${encodeURIComponent(sessionId)}`);
  }, [sessionId, openWeb]);

  // Notification stream (SPEC §21.7): toasts for approvals raised while the overlay is
  // hidden / on another session, and for cron and project results.
  useOverlayNotifications({
    enabled: auth === "in",
    sessionId,
    onAuthLost: goLoggedOut,
    onResync: chat.resyncCurrent,
    onClick: (target) => {
      if (target.type === "web") openWeb(target.path);
      else {
        // the shell already brought the window back; make sure the card is loaded
        setPanelOpen(false);
        chat.resyncCurrent();
      }
    },
  });

  const banner =
    capture.note ||
    (chat.connection === "offline" && auth === "in" ? "Không kết nối được backend — đang thử lại…" : note);

  const avatarReady = Boolean(agent?.avatar_ready && agent.avatar_config);

  // overflow-hidden: the overlay is a fixed-size window, never a scrolling page.
  // Absolutely-positioned descendants such as the `sr-only` spans in the panel's
  // message list would otherwise have no positioned ancestor and stretch the
  // document below the visible area. w-full, not w-dvw: dvw includes the
  // scrollbar and caused a horizontal scrollbar in a browser tab.
  return (
    <div ref={rootRef} className="relative flex h-dvh w-full select-none flex-row overflow-hidden">
      <div className="relative flex h-full w-[360px] shrink-0 flex-col justify-end gap-2 p-2">
        {auth === "out" && <OverlayLogin onDone={() => setAuth("in")} />}

        {banner && auth !== "out" && (
          <div {...hit} className="rounded-xl bg-black/75 px-3 py-1.5 text-xs text-white">
            {banner}
          </div>
        )}

        {/* Fixed zone above the model, reserved even when empty: the bubble sits
            here instead of on top of the avatar, and appearing/disappearing never
            resizes the canvas. It is transparent and click-through — only the
            bubble itself is a hit region. */}
        <div className="flex shrink-0 items-end" style={{ height: BUBBLE_ZONE_PX }}>
          {bubble && auth === "in" && (
            <div className="max-h-full w-full">
              <OverlayBubble
                bubble={bubble}
                onOpenPanel={() => setPanelOpen(true)}
                onDecision={(id, decision) => chat.decideApproval(id, decision)}
                onHover={setHovering}
              />
            </div>
          )}
        </div>

        {avatarReady && agent ? (
          <div {...model} data-avatar-fps={avatarFps} className="min-h-0 flex-1">
            <AvatarCanvas
              agentId={agent.id}
              avatarConfig={agent.avatar_config as unknown as AvatarConfig}
              aiState={aiState}
              maxFps={avatarFps}
              className="h-full w-full"
            />
          </div>
        ) : (
          <div className="min-h-0 flex-1" />
        )}

        {auth === "in" && (
          <OverlayInput
            value={draft}
            onChange={setDraft}
            busy={chat.busy || uploading}
            disabled={!sessionId}
            canSend={draft.trim().length > 0 || capture.image !== null}
            panelOpen={panelOpen}
            image={capture.image}
            capturing={capture.capturing}
            captureDisabledReason={
              !getDesktop()
                ? "Chụp màn hình chỉ có trong app desktop"
                : !visionOk
                  ? "Agent này chưa bật vision"
                  : null
            }
            onSend={() => void submit()}
            onStop={chat.abort}
            onTogglePanel={() => setPanelOpen((v) => !v)}
            onCapture={() => void capture.begin()}
            onClearImage={capture.clear}
          />
        )}
      </div>

      {panelOpen && auth === "in" && (
        <OverlayPanel
          messages={chat.messages}
          streamingText={chat.live.streamingText}
          agents={agents}
          agentId={agent?.id ?? null}
          onSelectAgent={selectAgent}
          onOpenHistory={openHistory}
          onClose={() => setPanelOpen(false)}
        />
      )}
    </div>
  );
}
