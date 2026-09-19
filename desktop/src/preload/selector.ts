// Preload of the region-selector window (SPEC §21.9). Same rule as the overlay's
// preload: a fixed object through contextBridge, never the raw ipcRenderer.
import { contextBridge, ipcRenderer, type IpcRendererEvent } from "electron";

contextBridge.exposeInMainWorld("pagiSelector", {
  /** the frozen screenshot of this display, as a data URL */
  onImage: (cb: (dataUrl: string) => void) => {
    ipcRenderer.on("selector:image", (_e: IpcRendererEvent, dataUrl: string) => cb(dataUrl));
  },
  /** the chosen rectangle in this window's CSS pixels, or null if cancelled */
  done: (rect: { x: number; y: number; width: number; height: number } | null) =>
    ipcRenderer.send("selector:done", rect),
});
