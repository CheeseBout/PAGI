// Launcher: VS Code / Claude Code terminals export ELECTRON_RUN_AS_NODE=1,
// which makes Electron behave as plain Node (`require("electron").app` is
// undefined). Strip it before starting electron-vite.
import { spawn } from "node:child_process";

const env = { ...process.env };
delete env.ELECTRON_RUN_AS_NODE;

const child = spawn("npx", ["electron-vite", process.argv[2] || "dev"], {
  stdio: "inherit",
  env,
  shell: true,
});
child.on("exit", (code) => process.exit(code ?? 0));
