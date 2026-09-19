// The always-visible input row under the avatar (SPEC §21.4.1): a textarea that
// grows to 4 lines, Enter to send / Shift+Enter for a newline, the send button
// turning into Stop while a reply is running, the panel toggle, and (SPEC §21.9)
// the region-screenshot button with a preview chip for the pending image.
import { ArrowUp, Crop, PanelRightClose, PanelRightOpen, Square, X } from "lucide-react";
import { useLayoutEffect, useRef, type KeyboardEvent } from "react";
import { HIT_ATTR } from "@/features/overlay/hitTest";
import type { CapturedImage } from "@/features/overlay/useScreenCapture";

const hit = { [HIT_ATTR]: "" };
const MAX_ROWS = 4;

export default function OverlayInput({
  value,
  onChange,
  busy,
  disabled,
  canSend,
  panelOpen,
  image,
  capturing,
  captureDisabledReason,
  onSend,
  onStop,
  onTogglePanel,
  onCapture,
  onClearImage,
}: {
  value: string;
  onChange: (v: string) => void;
  busy: boolean;
  disabled: boolean;
  /** something to send: text and/or a pending screenshot */
  canSend: boolean;
  panelOpen: boolean;
  image: CapturedImage | null;
  capturing: boolean;
  /** why the capture button is off (shown as its tooltip), or null when it works */
  captureDisabledReason: string | null;
  onSend: () => void;
  onStop: () => void;
  onTogglePanel: () => void;
  onCapture: () => void;
  onClearImage: () => void;
}) {
  const ref = useRef<HTMLTextAreaElement | null>(null);

  // auto-grow up to MAX_ROWS lines, then scroll
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    const line = parseFloat(getComputedStyle(el).lineHeight) || 20;
    el.style.height = `${Math.min(el.scrollHeight, line * MAX_ROWS)}px`;
  }, [value]);

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    // IME composition (Vietnamese Telex etc.) must not submit on its Enter
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      if (!busy && !disabled) onSend();
    }
  }

  return (
    <div {...hit} className="flex flex-col gap-1.5 rounded-2xl border border-border bg-bg-elev p-1.5 shadow-lg">
      {image && (
        <div className="flex items-center gap-2 px-1">
          <img
            src={image.url}
            alt="Ảnh chụp đang chờ gửi"
            className="h-12 max-w-[120px] rounded-md border border-border object-cover"
          />
          <span className="flex-1 text-xs text-muted">Ảnh sẽ được gửi kèm tin nhắn</span>
          <button
            onClick={onClearImage}
            title="Bỏ ảnh"
            aria-label="Bỏ ảnh chụp"
            className="grid size-6 place-items-center rounded-full text-muted hover:bg-bg-alt"
          >
            <X className="size-3.5" />
          </button>
        </div>
      )}

      <div className="flex items-end gap-1.5">
        <textarea
          ref={ref}
          rows={1}
          value={value}
          disabled={disabled}
          placeholder={disabled ? "Đang kết nối…" : "Nhắn cho agent…"}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={onKeyDown}
          className="max-h-24 min-h-8 flex-1 resize-none bg-transparent px-2 py-1 text-sm leading-5 outline-none"
        />
        <button
          onClick={onCapture}
          disabled={disabled || capturing || captureDisabledReason !== null}
          title={captureDisabledReason ?? "Chụp một vùng màn hình"}
          aria-label="Chụp vùng màn hình"
          className="grid size-8 place-items-center rounded-full text-muted hover:bg-bg-alt disabled:opacity-40"
        >
          <Crop className="size-4" />
        </button>
        {busy ? (
          <button
            onClick={onStop}
            title="Dừng"
            aria-label="Dừng"
            className="grid size-8 place-items-center rounded-full bg-danger text-white"
          >
            <Square className="size-3.5" fill="currentColor" />
          </button>
        ) : (
          <button
            onClick={onSend}
            disabled={disabled || !canSend}
            title="Gửi (Enter)"
            aria-label="Gửi"
            className="grid size-8 place-items-center rounded-full bg-accent text-accent-fg disabled:opacity-40"
          >
            <ArrowUp className="size-4" />
          </button>
        )}
        <button
          onClick={onTogglePanel}
          title={panelOpen ? "Thu gọn" : "Mở panel"}
          aria-label={panelOpen ? "Thu gọn panel" : "Mở panel"}
          className="grid size-8 place-items-center rounded-full text-muted hover:bg-bg-alt"
        >
          {panelOpen ? <PanelRightClose className="size-4" /> : <PanelRightOpen className="size-4" />}
        </button>
      </div>
    </div>
  );
}
