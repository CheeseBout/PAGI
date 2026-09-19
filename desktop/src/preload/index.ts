// Preload (SPEC §21.11): the ONLY surface the renderer gets. A fixed object via
// contextBridge — never the raw ipcRenderer, `require`, or any Node API.
import { contextBridge, ipcRenderer, type IpcRendererEvent } from "electron";

type Mode = "compact" | "bubble" | "panel";

contextBridge.exposeInMainWorld("pagiDesktop", {
  setMode: (mode: Mode) => ipcRenderer.send("overlay:set-mode", mode),
  setInteractive: (interactive: boolean) => ipcRenderer.send("overlay:set-interactive", interactive),
  dragStart: () => ipcRenderer.send("overlay:drag-start"),
  dragMove: (dx: number, dy: number) => ipcRenderer.send("overlay:drag-move", dx, dy),
  dragEnd: () => ipcRenderer.send("overlay:drag-end"),
  openExternal: (url: string) => ipcRenderer.send("overlay:open-external", url),
  showNotification: (n: { title: string; body: string; kind: string; target: unknown; tag?: string }) =>
    ipcRenderer.send("overlay:show-notification", n),
  closeNotification: (tag: string) => ipcRenderer.send("overlay:close-notification", tag),
  startCapture: (): Promise<ArrayBuffer | null> => ipcRenderer.invoke("overlay:start-capture"),
  onCaptureHotkey: (cb: () => void) => {
    const listener = () => cb();
    ipcRenderer.on("overlay:capture-hotkey", listener);
    return () => ipcRenderer.removeListener("overlay:capture-hotkey", listener);
  },
  onVisibilityChange: (cb: (visible: boolean) => void) => {
    const listener = (_e: IpcRendererEvent, visible: boolean) => cb(visible);
    ipcRenderer.on("overlay:visibility", listener);
    return () => ipcRenderer.removeListener("overlay:visibility", listener);
  },
  onNotificationClick: (cb: (click: { kind: string; target: unknown }) => void) => {
    // wrap so the renderer never receives the raw IpcRendererEvent
    const listener = (_e: IpcRendererEvent, click: { kind: string; target: unknown }) => cb(click);
    ipcRenderer.on("overlay:notification-click", listener);
    return () => ipcRenderer.removeListener("overlay:notification-click", listener);
  },
  getInfo: (): Promise<{ version: string; platform: string }> => ipcRenderer.invoke("overlay:get-info"),
});
