// PAGI desktop overlay — main process (SPEC §21). Deliberately thin: window,
// tray, hotkey, click-through and drag plumbing. All UI and business logic
// lives in the frontend's /overlay route; all decisions that can be pure live
// in ./policy.ts. Phase 20a scope (spike): transparent window + avatar,
// click-through, drag, tray, toggle hotkey, resource metrics.
import {
  app,
  BrowserWindow,
  globalShortcut,
  ipcMain,
  Menu,
  nativeImage,
  Notification,
  screen,
  session,
  shell,
  Tray,
} from "electron";
import type { IpcMainEvent, IpcMainInvokeEvent } from "electron";
import fs from "node:fs";
import path from "node:path";
import { captureRegion } from "./capture";
import {
  clampToArea,
  isAllowedExternalUrl,
  isAllowedNavigation,
  isInsecureRemote,
  isOverlayMode,
  isTrustedSender,
  originOf,
  parseNotification,
  restoreRect,
  sizeForMode,
  type OverlayMode,
  type Rect,
} from "./policy";

const OVERLAY_URL = (process.env.PAGI_OVERLAY_URL || "http://localhost:5173").replace(/\/+$/, "");
const HOTKEY_TOGGLE = process.env.PAGI_OVERLAY_HOTKEY_TOGGLE || "CommandOrControl+Alt+P";
const HOTKEY_CAPTURE = process.env.PAGI_OVERLAY_HOTKEY_CAPTURE || "CommandOrControl+Alt+X";
const ORIGIN = originOf(OVERLAY_URL) ?? "http://localhost:5173";
const PARTITION = "persist:pagi"; // persistent cookie jar (SPEC §21.6)
const RETRY_LOAD_MS = 3000;
// Test seam: when set, toasts are appended to this file as JSON lines and NOT shown
// natively (electron.Notification cannot be stubbed from outside), and the live
// toast map is exposed as globalThis.__pagiToasts so a test can emit "click".
const TOAST_LOG = process.env.PAGI_OVERLAY_TOAST_LOG || "";

let win: BrowserWindow | null = null;
let tray: Tray | null = null;
let mode: OverlayMode = "compact";
let dragOrigin: Rect | null = null;
let metricsTimer: NodeJS.Timeout | null = null;
// live toasts by tag (e.g. approval id) so `approval_resolved` can close the exact one
const toasts = new Map<string, Notification>();

// ── persisted window position (SPEC §21.3) ──────────────────────────────
const statePath = () => path.join(app.getPath("userData"), "window-state.json");

function loadSavedPosition(): Rect | null {
  try {
    const raw = JSON.parse(fs.readFileSync(statePath(), "utf-8")) as Partial<Rect>;
    if (typeof raw.x === "number" && typeof raw.y === "number") {
      return { x: raw.x, y: raw.y, width: 0, height: 0 };
    }
  } catch {
    /* first run or unreadable — fall back to the default corner */
  }
  return null;
}

function savePosition(): void {
  if (!win || win.isDestroyed()) return;
  const [x, y] = win.getPosition();
  try {
    fs.writeFileSync(statePath(), JSON.stringify({ x, y }));
  } catch (err) {
    console.warn("[overlay] could not save window position", err);
  }
}

function workAreas(): Rect[] {
  return screen.getAllDisplays().map((d) => d.workArea);
}

// ── window ──────────────────────────────────────────────────────────────
function createWindow(): void {
  const size = sizeForMode(mode);
  const rect = restoreRect(loadSavedPosition(), size, workAreas(), screen.getPrimaryDisplay().workArea);

  win = new BrowserWindow({
    ...rect,
    transparent: true,
    frame: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    hasShadow: false,
    resizable: false,
    show: false,
    backgroundColor: "#00000000",
    webPreferences: {
      preload: path.join(__dirname, "../preload/index.js"),
      partition: PARTITION,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      allowRunningInsecureContent: false,
      spellcheck: false,
      // hidden window => Chromium pauses rAF, so the Live2D loop stops (SPEC §21.12)
      backgroundThrottling: true,
      devTools: process.env.PAGI_OVERLAY_DEVTOOLS === "1",
    },
  });

  win.setAlwaysOnTop(true, "screen-saver");
  // click-through by default; the renderer flips it over the model/inputs (SPEC §21.3.2)
  win.setIgnoreMouseEvents(true, { forward: true });

  win.webContents.on("will-navigate", (e, url) => {
    if (!isAllowedNavigation(url, ORIGIN)) e.preventDefault();
  });
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (isAllowedExternalUrl(url, ORIGIN)) void shell.openExternal(url);
    return { action: "deny" };
  });

  // backend not up yet (user starts it themselves): retry instead of a blank window
  win.webContents.on("did-fail-load", (_e, code, desc, url, isMainFrame) => {
    if (!isMainFrame || code === -3 /* aborted */) return;
    console.warn(`[overlay] load failed (${desc}) ${url}; retrying in ${RETRY_LOAD_MS}ms`);
    setTimeout(() => {
      if (win && !win.isDestroyed()) void win.loadURL(`${OVERLAY_URL}/overlay`);
    }, RETRY_LOAD_MS);
  });

  win.once("ready-to-show", () => win?.show());
  // The page cannot tell for itself: document.hidden stays false after win.hide()
  // on Electron/Windows, so the shell reports visibility explicitly (SPEC §21.8).
  const sendVisibility = (visible: boolean) => {
    if (win && !win.isDestroyed()) win.webContents.send("overlay:visibility", visible);
  };
  win.on("show", () => sendVisibility(true));
  win.on("hide", () => sendVisibility(false));
  win.on("moved", savePosition);
  win.on("closed", () => {
    win = null;
  });

  void win.loadURL(`${OVERLAY_URL}/overlay`);
}

function toggleWindow(): void {
  if (!win || win.isDestroyed()) return;
  if (win.isVisible()) win.hide();
  else win.show();
}

// ── IPC (SPEC §21.11): every handler checks the sender's origin ─────────
function trusted(e: IpcMainEvent | IpcMainInvokeEvent): boolean {
  return isTrustedSender(e.senderFrame?.url, ORIGIN);
}

function registerIpc(): void {
  ipcMain.on("overlay:set-interactive", (e, interactive: unknown) => {
    if (!trusted(e) || typeof interactive !== "boolean" || !win) return;
    win.setIgnoreMouseEvents(!interactive, { forward: true });
  });

  ipcMain.on("overlay:set-mode", (e, next: unknown) => {
    if (!trusted(e) || !isOverlayMode(next) || !win) return;
    mode = next;
    const size = sizeForMode(next);
    const [x, y] = win.getPosition();
    const area = screen.getDisplayNearestPoint({ x, y }).workArea;
    // resizable:false blocks user resizing only; programmatic setBounds still works
    win.setBounds(clampToArea({ x, y, ...size }, area));
  });

  // Dragging is driven by the renderer (mousedown on the model); it sends the
  // delta from the press point, so no cursor polling here. -webkit-app-region
  // is not used: it swallows mouse events and would break click-through.
  ipcMain.on("overlay:drag-start", (e) => {
    if (!trusted(e) || !win) return;
    const { x, y, width, height } = win.getBounds();
    dragOrigin = { x, y, width, height };
  });
  ipcMain.on("overlay:drag-move", (e, dx: unknown, dy: unknown) => {
    if (!trusted(e) || !win || !dragOrigin) return;
    if (typeof dx !== "number" || typeof dy !== "number" || !Number.isFinite(dx) || !Number.isFinite(dy)) return;
    // setBounds with the size frozen at drag start, not setPosition: on Windows
    // with a scaled display setPosition lets the size drift by a pixel or two
    // per call, which resizes the canvas every move and makes the model flicker.
    win.setBounds({
      x: Math.round(dragOrigin.x + dx),
      y: Math.round(dragOrigin.y + dy),
      width: dragOrigin.width,
      height: dragOrigin.height,
    });
  });
  ipcMain.on("overlay:drag-end", (e) => {
    if (!trusted(e)) return;
    dragOrigin = null;
    savePosition();
  });

  ipcMain.on("overlay:open-external", (e, url: unknown) => {
    if (!trusted(e) || typeof url !== "string" || url.length > 2048) return;
    // http(s) URLs of the overlay origin only (SPEC §21.11)
    if (isAllowedExternalUrl(url, ORIGIN)) void shell.openExternal(url);
  });

  ipcMain.on("overlay:show-notification", (e, raw: unknown) => {
    if (!trusted(e) || !Notification.isSupported()) return;
    const n = parseNotification(raw);
    if (!n) return;
    const toast = new Notification({ title: n.title, body: n.body });
    if (n.tag) {
      toasts.get(n.tag)?.close(); // a re-notify replaces, never stacks
      toasts.set(n.tag, toast);
    }
    const forget = () => {
      if (n.tag && toasts.get(n.tag) === toast) toasts.delete(n.tag);
    };
    toast.on("click", () => {
      forget();
      // bring the overlay back, then tell the page what was clicked (SPEC §21.8.2)
      if (win && !win.isDestroyed()) {
        win.show();
        win.webContents.send("overlay:notification-click", { kind: n.kind, target: n.target });
      }
    });
    toast.on("close", forget);
    if (TOAST_LOG) {
      (globalThis as Record<string, unknown>).__pagiToasts = toasts;
      try {
        fs.appendFileSync(
          TOAST_LOG,
          JSON.stringify({ title: n.title, body: n.body, kind: n.kind, tag: n.tag, target: n.target }) + "\n",
        );
      } catch {
        /* test seam only */
      }
    } else {
      toast.show();
    }
  });

  ipcMain.on("overlay:close-notification", (e, tag: unknown) => {
    if (!trusted(e) || typeof tag !== "string") return;
    toasts.get(tag)?.close();
    toasts.delete(tag);
  });

  // Region screenshot (SPEC §21.9). Returns PNG bytes, or null if the user cancelled.
  // The page decides when to call this (button, hotkey relay, /screen) — never a timer.
  ipcMain.handle("overlay:start-capture", async (e): Promise<ArrayBuffer | null> => {
    if (!trusted(e)) throw new Error("untrusted sender");
    const png = await captureRegion(win);
    if (!png) return null;
    return png.buffer.slice(png.byteOffset, png.byteOffset + png.byteLength) as ArrayBuffer;
  });

  ipcMain.handle("overlay:get-info", (e) => {
    if (!trusted(e)) throw new Error("untrusted sender");
    return { version: app.getVersion(), platform: process.platform };
  });
}

// ── logout (SPEC §21.6) ─────────────────────────────────────────────────
// The session is a stateless cookie, so "logging out" = telling the server (harmless
// no-op for a JWT) and, what actually matters, dropping this window's cookie jar.
// The page reloads, finds itself unauthenticated and shows the login form.
async function logout(): Promise<void> {
  const ses = session.fromPartition(PARTITION);
  try {
    await ses.fetch(`${OVERLAY_URL}/api/auth/logout`, { method: "POST" });
  } catch {
    /* backend down: still clear locally */
  }
  await ses.clearStorageData({ storages: ["cookies"] });
  if (win && !win.isDestroyed()) {
    win.webContents.reload();
    win.show();
  }
}

// ── tray + hotkey ───────────────────────────────────────────────────────
function createTray(): void {
  const iconPath = path.join(app.getAppPath(), "resources", "tray.png");
  const icon = fs.existsSync(iconPath) ? nativeImage.createFromPath(iconPath) : nativeImage.createEmpty();
  tray = new Tray(icon);
  tray.setToolTip("PAGI");
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: "Show / Hide", click: toggleWindow },
      { label: "Open web UI", click: () => void shell.openExternal(OVERLAY_URL) },
      { label: "Đăng xuất", click: () => void logout() },
      { type: "separator" },
      // 20a measurement aid (SPEC §21.12): samples every 10s into metrics.log
      {
        label: "Log CPU/RAM metrics",
        type: "checkbox",
        checked: metricsTimer !== null,
        click: (item) => setMetrics(item.checked),
      },
      { label: "Open metrics folder", click: () => void shell.openPath(app.getPath("userData")) },
      { type: "separator" },
      { label: "Quit", click: () => app.quit() },
    ]),
  );
  tray.on("click", toggleWindow);
}

// ── resource metrics for the 20a measurement (SPEC §21.12) ──────────────
const metricsPath = () => path.join(app.getPath("userData"), "metrics.log");

function sampleMetrics(): void {
  const metrics = app.getAppMetrics();
  const cpu = metrics.reduce((s, m) => s + m.cpu.percentCPUUsage, 0);
  // privateBytes (Windows) doesn't double-count memory shared between the
  // Chromium processes, unlike workingSetSize — closer to what the app costs.
  const privMb = metrics.reduce((s, m) => s + (m.memory.privateBytes ?? 0), 0) / 1024;
  const wsMb = metrics.reduce((s, m) => s + m.memory.workingSetSize, 0) / 1024;
  const visible = win?.isVisible() ? "visible" : "hidden";
  // per-process split (type:privateMB) — tells whether the cost sits in the
  // renderer (JS/DOM), the GPU process (textures) or the browser process
  const perProc = metrics
    .map((m) => `${m.type}:${((m.memory.privateBytes ?? 0) / 1024).toFixed(0)}MB`)
    .join(" ");
  const line =
    `${new Date().toISOString()} ${visible} cpu=${cpu.toFixed(1)}% ` +
    `private=${privMb.toFixed(0)}MB workingSet=${wsMb.toFixed(0)}MB procs=${metrics.length} [${perProc}]`;
  console.log(`[metrics] ${line}`);
  try {
    fs.appendFileSync(metricsPath(), line + "\n");
  } catch {
    /* logging must never break the app */
  }
}

function setMetrics(on: boolean): void {
  if (on && !metricsTimer) {
    sampleMetrics();
    metricsTimer = setInterval(sampleMetrics, 10_000);
  } else if (!on && metricsTimer) {
    clearInterval(metricsTimer);
    metricsTimer = null;
  }
}

// ── lifecycle ───────────────────────────────────────────────────────────
if (!app.requestSingleInstanceLock()) {
  app.quit(); // a second launch just surfaces the first (below)
} else {
  app.on("second-instance", () => {
    if (win && !win.isDestroyed()) win.show();
  });

  // Windows needs an AppUserModelID for toasts to show (notably in dev)
  app.setAppUserModelId("com.pagi.desktop");

  app.whenReady().then(() => {
    if (isInsecureRemote(OVERLAY_URL)) {
      console.warn(`[overlay] ${OVERLAY_URL} is plain HTTP on a non-loopback host: the session cookie is unencrypted`);
    }
    // deny every web permission request (camera, mic, web notifications…)
    session.fromPartition(PARTITION).setPermissionRequestHandler((_wc, _perm, cb) => cb(false));

    registerIpc();
    createWindow();
    // Test seam (Playwright): expose the tray actions, which have no other way in.
    if (process.env.PAGI_OVERLAY_TEST_HOOKS === "1") {
      (globalThis as Record<string, unknown>).__pagiTest = { logout };
    }
    if (!globalShortcut.register(HOTKEY_TOGGLE, toggleWindow)) {
      console.warn(`[overlay] could not register hotkey ${HOTKEY_TOGGLE} (already taken?)`);
    }
    // The hotkey only *asks the page* to start a capture, so the same checks run as for
    // the button (vision enabled, one capture at a time) — the shell never captures by itself.
    if (
      !globalShortcut.register(HOTKEY_CAPTURE, () => {
        if (win && !win.isDestroyed()) win.webContents.send("overlay:capture-hotkey");
      })
    ) {
      console.warn(`[overlay] could not register hotkey ${HOTKEY_CAPTURE} (already taken?)`);
    }
    createTray();
    if (process.env.PAGI_OVERLAY_METRICS === "1") setMetrics(true);
  });

  app.on("will-quit", () => globalShortcut.unregisterAll());
  // tray app: closing the window never quits; only the tray's Quit does
  app.on("window-all-closed", () => {});
}
