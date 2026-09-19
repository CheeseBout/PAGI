import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Dev proxy: forward /api and /ws to the backend so the SPA stays same-origin.
// `overrides.vite` in package.json pins a single vite copy across the whole
// dependency tree — without that, npm can nest a second `vite` under
// `vitest`, and TypeScript then treats vitest/config's re-exported
// `defineConfig`/`Plugin` types as incompatible with @vitejs/plugin-react's.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
      // Cubism Web Framework's own source uses this alias internally
      // (matches the SDK's own Samples/TypeScript/Demo/vite.config.mts) —
      // 2D_PLAN.md §3.2.
      "@framework": path.resolve(__dirname, "./src/vendor/live2d/framework/src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
      "/ws": { target: "ws://localhost:8000", ws: true },
      "/avatars": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
  test: {
    environment: "jsdom",
    // Playwright specs live in tests/e2e and must not be collected by vitest.
    exclude: ["**/node_modules/**", "**/dist/**", "tests/e2e/**"],
  },
});
