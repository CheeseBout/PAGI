// Runs every e2e script in order and prints one summary. Exit code 1 if any failed.
// Needs: backend on :8000, frontend dev server on :5173, `npm run build` done in desktop/.
const { spawnSync } = require("child_process");
const path = require("path");

const SCRIPTS = [
  ["ui.cjs", "overlay UI in a browser (mocked chat/notification sockets)"],
  ["capture-ui.cjs", "screenshot flow in the page (stubbed capture, upload intercepted)"],
  ["shell.cjs", "the real Electron shell"],
  ["capture-shell.cjs", "real region capture on Electron (upload/chat intercepted)"],
  ["perf-logout-shell.cjs", "idle frame-rate + tray logout on Electron"],
];

// Electron behaves as plain Node if this is set (VS Code / Claude Code terminals set it)
const env = { ...process.env };
delete env.ELECTRON_RUN_AS_NODE;

const only = process.argv.slice(2);
let failed = 0;
for (const [file, what] of SCRIPTS) {
  if (only.length && !only.some((o) => file.includes(o))) continue;
  console.log(`\n━━━ ${file} — ${what}`);
  const r = spawnSync(process.execPath, [path.join(__dirname, file)], { stdio: "inherit", env });
  if (r.status !== 0) {
    failed++;
    console.log(`✗ ${file} exited with ${r.status}`);
  }
}
console.log(failed ? `\n${failed} script(s) failed` : "\nall e2e scripts passed");
process.exit(failed ? 1 : 0);
