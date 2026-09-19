// Region screenshot (SPEC §21.9). Only ever started by an explicit user gesture —
// the renderer's button, the capture hotkey, or the `/screen` command — never by a
// timer and never by the model. The picture lives in memory only: it is returned
// to the renderer as PNG bytes and nothing is written to disk here.
import { BrowserWindow, desktopCapturer, ipcMain, screen, type Display, type NativeImage } from "electron";
import path from "node:path";
import { app } from "electron";
import { cropRectFromSelection, fitLongestEdge } from "./policy";

const SELECT_TIMEOUT_MS = 120_000; // an abandoned selector must not stay over the screen forever
const HIDE_SETTLE_MS = 180; // let the compositor drop the overlay before grabbing the screen

let capturing = false;

interface Target {
  display: Display;
  image: NativeImage;
  win: BrowserWindow;
}

function selectorPreload(): string {
  return path.join(__dirname, "../preload/selector.js");
}

function selectorHtml(): string {
  return path.join(app.getAppPath(), "resources", "selector.html");
}

/** Freeze every display, let the user drag a region on one of them, return PNG bytes (or null). */
export async function captureRegion(overlay: BrowserWindow | null): Promise<Buffer | null> {
  if (capturing) return null; // one capture at a time (second gesture is ignored)
  capturing = true;
  const overlayWasVisible = !!overlay && !overlay.isDestroyed() && overlay.isVisible();
  const targets: Target[] = [];

  try {
    // 1. hide the overlay so it can't end up in the picture
    if (overlay && !overlay.isDestroyed() && overlayWasVisible) {
      overlay.hide();
      await new Promise((r) => setTimeout(r, HIDE_SETTLE_MS));
    }

    // 2. one frozen image per display, at the display's physical resolution
    const displays = screen.getAllDisplays();
    const maxW = Math.max(...displays.map((d) => Math.round(d.size.width * d.scaleFactor)));
    const maxH = Math.max(...displays.map((d) => Math.round(d.size.height * d.scaleFactor)));
    const sources = await desktopCapturer.getSources({
      types: ["screen"],
      thumbnailSize: { width: maxW, height: maxH },
    });

    for (const [i, display] of displays.entries()) {
      const src = sources.find((s) => s.display_id === String(display.id)) ?? sources[i];
      if (!src || src.thumbnail.isEmpty()) continue;
      const win = new BrowserWindow({
        ...display.bounds,
        // fullscreen: a plain frameless window is clamped to the display's WORK AREA by
        // Windows (no taskbar strip), which would leave part of the screen unselectable
        fullscreen: true,
        frame: false,
        alwaysOnTop: true,
        skipTaskbar: true,
        hasShadow: false,
        show: false,
        backgroundColor: "#000000",
        webPreferences: {
          preload: selectorPreload(),
          contextIsolation: true,
          nodeIntegration: false,
          sandbox: true,
          webSecurity: true,
          devTools: false,
        },
      });
      win.setAlwaysOnTop(true, "screen-saver");
      // a selector page never navigates anywhere or opens anything
      win.webContents.on("will-navigate", (e) => e.preventDefault());
      win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
      targets.push({ display, image: src.thumbnail, win });
    }
    if (targets.length === 0) return null;

    // 3. show the frozen image on each display and wait for one selection
    const result = await new Promise<{ target: Target; sel: unknown } | null>((resolve) => {
      let settled = false;
      const finish = (v: { target: Target; sel: unknown } | null) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        ipcMain.removeListener("selector:done", onDone);
        resolve(v);
      };
      const onDone = (e: Electron.IpcMainEvent, sel: unknown) => {
        // only our own selector windows may answer
        const target = targets.find((t) => !t.win.isDestroyed() && t.win.webContents.id === e.sender.id);
        if (!target) return;
        finish(sel === null ? null : { target, sel });
      };
      const timer = setTimeout(() => finish(null), SELECT_TIMEOUT_MS);
      ipcMain.on("selector:done", onDone);

      for (const t of targets) {
        t.win.once("closed", () => {
          if (targets.every((x) => x.win.isDestroyed())) finish(null);
        });
        void t.win.loadFile(selectorHtml()).then(() => {
          if (t.win.isDestroyed()) return;
          // JPEG for display only: a 4K PNG data URL would be tens of MB over IPC.
          // The bytes we return are cropped from the original NativeImage below.
          t.win.webContents.send("selector:image", `data:image/jpeg;base64,${t.image.toJPEG(85).toString("base64")}`);
          t.win.show();
          t.win.focus();
        });
      }
    });

    if (!result) return null;

    // 4. crop the ORIGINAL image (physical pixels), bound its size, encode PNG.
    // The selection is in the selector window's own CSS pixels, and the frozen image was
    // stretched to that window — so map through the window's real size, not the display's
    // nominal one (they differ if the OS clamped the window).
    const { win: selWin, image } = result.target;
    const [cw, ch] = selWin.isDestroyed() ? [0, 0] : selWin.getContentSize();
    const rect = cropRectFromSelection(result.sel, { width: cw, height: ch }, image.getSize());
    if (!rect) return null; // too small (< 8px), garbage, or off-display
    let cropped = image.crop(rect);
    const fitted = fitLongestEdge(cropped.getSize());
    if (fitted.width !== cropped.getSize().width) cropped = cropped.resize({ ...fitted, quality: "best" });
    return cropped.toPNG();
  } finally {
    for (const t of targets) if (!t.win.isDestroyed()) t.win.destroy();
    // 5. bring the overlay back so the preview chip is visible (SPEC §21.9 step 3)
    if (overlay && !overlay.isDestroyed()) overlay.show();
    capturing = false;
  }
}
