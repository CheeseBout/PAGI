// e2e: perf-logout-shell.cjs — drives a REAL backend + frontend (see e2e/README.md). Cleans up what it creates.
// 20f on the REAL Electron shell (GPU rendering): idle frame-rate slowdown + tray logout.
const { _electron } = require("playwright");
const { BASE, USER, PASSWORD, DESKTOP, ELECTRON_EXE, SHOTS } = require("./config.cjs");
const fs = require("fs"), os = require("os"), path = require("path");
const results = [];
const check = (name, ok, detail = "") => { results.push({ name, ok }); console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  — " + detail : ""}`); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const userData = fs.mkdtempSync(path.join(os.tmpdir(), "pagi-f-"));
  const env = { ...process.env, PAGI_OVERLAY_TEST_HOOKS: "1" }; delete env.ELECTRON_RUN_AS_NODE;
  const app = await _electron.launch({ executablePath: ELECTRON_EXE, args: [".", `--user-data-dir=${userData}`], cwd: DESKTOP, env });
  const win = await app.firstWindow();
  await win.waitForSelector('input[type="password"]', { timeout: 30000 });
  await win.fill('input[type="password"]', PASSWORD);
  await win.click('button[type="submit"]');
  await win.waitForFunction(() => document.querySelector("textarea") && !document.querySelector("textarea").disabled, null, { timeout: 30000 });

  // ═══ A. idle frame-rate slowdown, real GPU ═══
  await win.goto(BASE + "/overlay?idleMs=2000");
  await win.waitForFunction(() => document.querySelector("[data-avatar-status]")?.dataset.avatarStatus === "ready", null, { timeout: 60000 });
  const fpsAttr = () => win.locator("[data-avatar-fps]").getAttribute("data-avatar-fps");
  const frames = () => win.evaluate(() => document.querySelector("canvas").__frames || 0);
  const rate = async (ms) => { const a = await frames(); await sleep(ms); return ((await frames()) - a) / (ms / 1000); };

  // The test runs on a live desktop: a real cursor crossing the window is real input and would
  // legitimately wake the avatar. So measure in 0.5s slices and keep only those where the cap stayed put.
  const slices = async (n, wantCap) => {
    const kept = [];
    for (let i = 0; i < n; i++) {
      const c0 = await fpsAttr(), f0 = await frames();
      await sleep(500);
      const c1 = await fpsAttr(), f1 = await frames();
      if (c0 === wantCap && c1 === wantCap) kept.push((f1 - f0) / 0.5);
    }
    kept.sort((a, b) => a - b);
    return { kept, median: kept.length ? kept[Math.floor(kept.length / 2)] : NaN };
  };
  const waitCap = async (want, ms) => { for (let t = 0; t < ms; t += 250) { if ((await fpsAttr()) === want) return true; await sleep(250); } return false; };

  await win.mouse.move(120, 300); // start from a known-active state
  await sleep(100);
  const act = await slices(4, "30");
  const fast = act.median;
  check("active: cap is 30 and ~30 frames/s are really rendered", act.kept.length >= 2 && fast > 22 && fast <= 34, `median ${fast} frames/s over ${act.kept.length} slices`);
  check("after the idle threshold the cap drops to 15", await waitCap("15", 8000), `cap=${await fpsAttr()}`);
  const idle = await slices(8, "15");
  const slow = idle.median;
  check("idle: ~15 frames/s are really rendered (half the active rate)", idle.kept.length >= 3 && slow > 9 && slow <= 17 && slow / fast < 0.65, `median ${slow} frames/s over ${idle.kept.length} quiet slices (active ${fast})`);
  await win.mouse.move(180, 400);
  await sleep(150);
  check("any pointer input restores the cap at once", (await fpsAttr()) === "30");
  const again = await slices(4, "30");
  check("and the real rate recovers to ~30", again.kept.length >= 2 && again.median > 22 && again.median <= 34, `median ${again.median} frames/s`);
  check("goes idle again after another quiet period", await waitCap("15", 8000));
  await win.keyboard.press("a");
  await sleep(150);
  check("a key press also restores full rate", (await fpsAttr()) === "30");

  // busy overrides idle: opening the panel counts as activity (no slowdown while it is up)
  await waitCap("15", 8000);
  await win.click('button[aria-label="Mở panel"]');
  await sleep(3500);
  check("with the panel open the avatar never slows down", (await fpsAttr()) === "30");
  await win.keyboard.press("Escape");

  // ═══ B. tray "Đăng xuất" ═══
  await win.goto(BASE + "/overlay");
  await win.waitForFunction(() => document.querySelector("textarea") && !document.querySelector("textarea").disabled, null, { timeout: 30000 });
  const cookieNames = () => app.evaluate(async ({ session }) => (await session.fromPartition("persist:pagi").cookies.get({})).map((c) => c.name));
  check("logged in: the session cookie is in the overlay's own cookie jar", (await cookieNames()).includes("pagi_session"));
  check("tray logout action is reachable (test hook)", await app.evaluate(() => typeof globalThis.__pagiTest?.logout === "function"));
  await app.evaluate(() => globalThis.__pagiTest.logout());
  await win.waitForSelector('input[type="password"]', { timeout: 15000 });
  check("after logout the cookie is gone", !(await cookieNames()).includes("pagi_session"), JSON.stringify(await cookieNames()));
  check("after logout the overlay shows the login form again (no chat input)", (await win.locator("textarea").count()) === 0);
  const me = await win.evaluate(async () => (await fetch("/api/auth/me", { credentials: "include" })).status);
  check("the server no longer recognises this window as logged in", me === 401, `GET /api/auth/me -> ${me}`);
  await win.fill('input[type="password"]', PASSWORD);
  await win.click('button[type="submit"]');
  await win.waitForFunction(() => document.querySelector("textarea") && !document.querySelector("textarea").disabled, null, { timeout: 30000 });
  check("and logging in again works", (await cookieNames()).includes("pagi_session"));

  const overlays = await win.evaluate(async () => (await fetch("/api/conversations?origin=overlay", { credentials: "include" })).json());
  for (const c of overlays) await win.evaluate((id) => fetch("/api/conversations/" + id, { method: "DELETE", credentials: "include" }), c.id);
  await app.close();
  try { fs.rmSync(userData, { recursive: true, force: true }); } catch {}
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
  process.exit(failed.length ? 1 : 0);
})().catch((e) => { console.error("SCRIPT ERROR", e); process.exit(2); });
