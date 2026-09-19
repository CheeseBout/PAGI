import { resolve } from "node:path";
import { defineConfig, externalizeDepsPlugin } from "electron-vite";

// No `renderer` section on purpose: the renderer is the frontend's /overlay
// route, loaded by URL (SPEC §21.2) — it is not bundled here.
export default defineConfig({
  main: { plugins: [externalizeDepsPlugin()] },
  preload: {
    plugins: [externalizeDepsPlugin()],
    build: {
      rollupOptions: {
        // two preloads: the overlay's own, and the one for the region-selector window
        input: {
          index: resolve(__dirname, "src/preload/index.ts"),
          selector: resolve(__dirname, "src/preload/selector.ts"),
        },
      },
    },
  },
});
