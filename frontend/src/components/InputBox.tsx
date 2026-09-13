import { FileText, Image, Paperclip, ScreenShare, ScreenShareOff, Square, X } from "lucide-react";
import { ChangeEvent, ClipboardEvent, DragEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import { api, ApiError, type Attachment } from "../api/client";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

export default function InputBox({
  onSend,
  onAbort,
  streaming,
  disabled,
  sessionId,
  onError,
}: {
  onSend: (text: string, attachmentIds: string[]) => void;
  onAbort: () => void;
  streaming: boolean;
  disabled?: boolean;
  sessionId: string | null;
  onError?: (msg: string) => void;
}) {
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const [sharingScreen, setSharingScreen] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);

  function stopScreenShare() {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
    setSharingScreen(false);
  }

  useEffect(() => stopScreenShare, []);

  async function toggleScreenShare() {
    if (sharingScreen) {
      stopScreenShare();
      return;
    }
    if (!navigator.mediaDevices?.getDisplayMedia) {
      onError?.("Screen sharing isn't supported in this browser (or requires HTTPS/localhost).");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getDisplayMedia({ video: true });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
      }
      stream.getVideoTracks()[0]?.addEventListener("ended", stopScreenShare);
      setSharingScreen(true);
    } catch {
      // user cancelled the picker, or the browser/context doesn't support it — no-op
    }
  }

  // Grabs the current shared-screen frame and uploads it as a normal image
  // attachment, so it flows through the same vision/HITL pipeline as any
  // other chat image — the agent only "sees" a fresh frame per sent message.
  async function captureScreenshot(): Promise<Attachment | null> {
    const video = videoRef.current;
    if (!sessionId || !video || !video.videoWidth) return null;
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext("2d")?.drawImage(video, 0, 0);
    const blob: Blob | null = await new Promise((resolve) =>
      canvas.toBlob(resolve, "image/jpeg", 0.85),
    );
    if (!blob) return null;
    const file = new File([blob], `screen-${Date.now()}.jpg`, { type: "image/jpeg" });
    try {
      return await api.uploadFile(sessionId, file);
    } catch (e) {
      onError?.(e instanceof ApiError ? e.message : "Screenshot upload failed");
      return null;
    }
  }

  async function addFiles(files: FileList | File[]) {
    if (!sessionId || disabled) return;
    setUploading(true);
    try {
      for (const f of Array.from(files)) {
        try {
          const att = await api.uploadFile(sessionId, f);
          setAttachments((prev) => [...prev, att]);
        } catch (e) {
          onError?.(e instanceof ApiError ? e.message : `Upload failed: ${f.name}`);
        }
      }
    } finally {
      setUploading(false);
    }
  }

  function onFileInput(e: ChangeEvent<HTMLInputElement>) {
    if (e.target.files?.length) addFiles(e.target.files);
    e.target.value = "";
  }

  function onPaste(e: ClipboardEvent<HTMLTextAreaElement>) {
    const files = Array.from(e.clipboardData.files);
    if (files.length) {
      e.preventDefault();
      addFiles(files);
    }
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    if (e.dataTransfer.files?.length) addFiles(e.dataTransfer.files);
  }

  async function removeAttachment(id: string) {
    setAttachments((prev) => prev.filter((a) => a.id !== id));
    if (sessionId) await api.deleteFile(sessionId, id).catch(() => {});
  }

  async function submit() {
    const t = text.trim();
    if (disabled || uploading) return;
    if (!t && attachments.length === 0 && !sharingScreen) return;
    const ids = attachments.map((a) => a.id);
    if (sharingScreen) {
      const shot = await captureScreenshot();
      if (shot) ids.push(shot.id);
    }
    if (!t && ids.length === 0) return;
    onSend(t, ids);
    setText("");
    setAttachments([]);
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    } else if (e.key === "Escape" && streaming) {
      e.preventDefault();
      onAbort();
    }
  }

  const canSend =
    !disabled && !uploading && (!!text.trim() || attachments.length > 0 || sharingScreen);

  return (
    <div
      className="input-box flex flex-col gap-2 border-t border-border px-4 py-3"
      onDrop={onDrop}
      onDragOver={(e) => e.preventDefault()}
    >
      {sharingScreen && (
        <div className="screen-share-banner flex items-center gap-1.5 text-xs text-accent">
          <ScreenShare className="size-3.5" /> Sharing your screen — every message you send includes a fresh
          screenshot.
        </div>
      )}
      {attachments.length > 0 && (
        <div className="composer-attachments flex flex-wrap gap-1.5">
          {attachments.map((a) => (
            <span
              key={a.id}
              className="attachment-chip inline-flex max-w-[260px] items-center gap-1.5 overflow-hidden text-ellipsis whitespace-nowrap rounded-full border border-border px-2 py-0.5 text-xs text-text"
              title={a.filename}
            >
              {a.kind === "image" ? <Image className="size-3.5 shrink-0" /> : <FileText className="size-3.5 shrink-0" />}{" "}
              {a.filename}
              <button
                onClick={() => removeAttachment(a.id)}
                aria-label="Remove attachment"
                className="text-muted hover:text-text"
              >
                <X className="size-3" />
              </button>
            </span>
          ))}
        </div>
      )}
      <div className="input-row flex items-end gap-2.5">
        <Button
          variant="outline"
          size="icon"
          className="attach-btn h-10 w-10 bg-transparent"
          onClick={() => fileRef.current?.click()}
          disabled={disabled || !sessionId}
          title="Attach a file"
        >
          <Paperclip className="size-4" />
        </Button>
        <input
          ref={fileRef}
          type="file"
          multiple
          hidden
          accept="image/*,text/*,application/pdf,application/json"
          onChange={onFileInput}
        />
        <Button
          variant="outline"
          size="icon"
          className={cn("share-screen-btn h-10 w-10", sharingScreen && "border-accent text-accent")}
          onClick={toggleScreenShare}
          disabled={disabled || !sessionId}
          title={sharingScreen ? "Stop sharing your screen" : "Share your screen with the agent"}
        >
          {sharingScreen ? <ScreenShareOff className="size-4" /> : <ScreenShare className="size-4" />}
        </Button>
        <video ref={videoRef} muted playsInline hidden />
        <Textarea
          className="max-h-[200px] min-h-[40px] flex-1 resize-none"
          value={text}
          placeholder={
            disabled
              ? "Select or start a conversation…"
              : uploading
                ? "Uploading…"
                : "Send a message (Enter to send, Shift+Enter for newline)"
          }
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
          rows={1}
          disabled={disabled}
        />
        {streaming ? (
          <Button variant="destructive" className="stop-btn" onClick={onAbort}>
            <Square className="size-3.5" /> Stop
          </Button>
        ) : (
          <Button className="send-btn" onClick={submit} disabled={!canSend}>
            Send
          </Button>
        )}
      </div>
    </div>
  );
}
