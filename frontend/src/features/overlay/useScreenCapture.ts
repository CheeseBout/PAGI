// Holds the (at most one) pending screenshot and starts captures (SPEC §21.9).
//
// PRIVACY INVARIANT: `begin()` is the only thing that calls desktop.startCapture(),
// and it is reached only from a user gesture — the capture button, the capture
// hotkey relayed by the shell, or the `/screen` command. No timer, no effect that
// runs on its own, nothing the model can trigger. The image stays here, in memory,
// until the user sends it or removes it; it is not uploaded before that.
import { useCallback, useEffect, useRef, useState } from "react";
import { getDesktop } from "@/lib/pagiDesktop";

export interface CapturedImage {
  blob: Blob;
  /** object URL for the preview chip; revoked when the image is dropped */
  url: string;
}

const NOTE_MS = 4000;

export function useScreenCapture(visionEnabled: boolean) {
  const [image, setImage] = useState<CapturedImage | null>(null);
  const [capturing, setCapturing] = useState(false);
  const [note, setNote] = useState("");
  const imageRef = useRef<CapturedImage | null>(null);
  const busyRef = useRef(false);
  const noteTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const flash = useCallback((text: string) => {
    setNote(text);
    if (noteTimer.current) clearTimeout(noteTimer.current);
    noteTimer.current = setTimeout(() => setNote(""), NOTE_MS);
  }, []);

  const drop = useCallback(() => {
    if (imageRef.current) URL.revokeObjectURL(imageRef.current.url);
    imageRef.current = null;
    setImage(null);
  }, []);

  /** Start a capture. Resolves true if a picture is now pending. */
  const begin = useCallback(async (): Promise<boolean> => {
    const desktop = getDesktop();
    if (!desktop) {
      flash("Chụp màn hình chỉ có trong app desktop.");
      return false;
    }
    if (!visionEnabled) {
      flash("Agent này chưa bật vision nên không nhận được ảnh.");
      return false;
    }
    if (busyRef.current) return false; // one capture at a time
    busyRef.current = true;
    setCapturing(true);
    try {
      const bytes = await desktop.startCapture();
      if (!bytes) return false; // cancelled (Esc / right-click / region too small)
      const replaced = imageRef.current !== null;
      if (imageRef.current) URL.revokeObjectURL(imageRef.current.url);
      const blob = new Blob([bytes], { type: "image/png" });
      const next = { blob, url: URL.createObjectURL(blob) };
      imageRef.current = next;
      setImage(next);
      if (replaced) flash("Đã thay ảnh chụp trước đó.");
      return true;
    } catch (e) {
      flash(`Không chụp được: ${e instanceof Error ? e.message : "lỗi không rõ"}`);
      return false;
    } finally {
      busyRef.current = false;
      setCapturing(false);
    }
  }, [visionEnabled, flash]);

  // the shell relays the global hotkey; it never captures on its own
  const beginRef = useRef(begin);
  beginRef.current = begin;
  useEffect(() => {
    const off = getDesktop()?.onCaptureHotkey(() => void beginRef.current());
    return () => off?.();
  }, []);

  useEffect(
    () => () => {
      if (imageRef.current) URL.revokeObjectURL(imageRef.current.url);
      if (noteTimer.current) clearTimeout(noteTimer.current);
    },
    [],
  );

  return { image, capturing, note, begin, clear: drop, flash };
}
