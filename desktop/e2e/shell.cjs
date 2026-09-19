// e2e: shell.cjs — drives a REAL backend + frontend (see e2e/README.md). Cleans up what it creates.
// Verifies the REAL Electron shell (desktop/) through Playwright's _electron driver.
const { _electron } = require("playwright");
const { BASE, USER, PASSWORD, DESKTOP, ELECTRON_EXE, SHOTS } = require("./config.cjs");
const OUT = SHOTS;
const fs = require("fs");
const os = require("os");
const path = require("path");


const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  — " + detail : ""}`);
};
const skip = (name, why) => console.log(`SKIP  ${name}  — ${why}`);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const userData = fs.mkdtempSync(path.join(os.tmpdir(), "pagi-e2e-"));
  const env = { ...process.env };
  delete env.ELECTRON_RUN_AS_NODE;
  const toastLog = path.join(userData, "toasts.log");
  env.PAGI_OVERLAY_TOAST_LOG = toastLog; // test seam: toasts are logged, not shown natively
  const app = await _electron.launch({
    executablePath: ELECTRON_EXE,
    args: [".", `--user-data-dir=${userData}`],
    cwd: DESKTOP,
    env,
  });
  const page = await app.firstWindow();
  page.on("pageerror", (e) => console.log("  [pageerror]", e.message));

  const bounds = () => app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].getBounds());

  // ── login inside the overlay, wait for the avatar ────────────────────
  await page.waitForSelector('input[type="password"]', { timeout: 30000 });
  await page.fill('input[type="password"]', PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForFunction(() => document.querySelector("textarea") && !document.querySelector("textarea").disabled, null, { timeout: 30000 });
  await page.waitForFunction(() => document.querySelector("[data-avatar-status]")?.dataset.avatarStatus === "ready", null, { timeout: 60000 });
  await sleep(1500);
  check("logged in inside the overlay window; avatar reached 'ready'", true);

  // ── window properties ────────────────────────────────────────────────
  const props = await app.evaluate(({ BrowserWindow }) => {
    const w = BrowserWindow.getAllWindows()[0];
    return { top: w.isAlwaysOnTop(), visible: w.isVisible(), resizable: w.isResizable(), b: w.getBounds(), n: BrowserWindow.getAllWindows().length };
  });
  check("single frameless overlay window, always-on-top, not user-resizable", props.n === 1 && props.top && props.visible && !props.resizable, JSON.stringify(props));
  check("compact size is 360x640", props.b.width === 360 && props.b.height === 640, `${props.b.width}x${props.b.height}`);

  // ── transparency: real pixels of the window ──────────────────────────
  const px = await app.evaluate(async ({ BrowserWindow }) => {
    const w = BrowserWindow.getAllWindows()[0];
    const img = await w.capturePage();
    const { width, height } = img.getSize();
    const bmp = img.toBitmap(); // BGRA
    const alphaAt = (x, y) => bmp[(y * width + x) * 4 + 3];
    let opaque = 0;
    for (let y = Math.floor(height * 0.35); y < Math.floor(height * 0.85); y += 4)
      for (let x = Math.floor(width * 0.3); x < Math.floor(width * 0.7); x += 4) if (alphaAt(x, y) > 0) opaque++;
    return { width, height, corner: alphaAt(2, 2), midLeft: alphaAt(3, Math.floor(height / 2)), opaque };
  });
  check("window is really transparent (alpha 0 at the corner and side margin)", px.corner === 0 && px.midLeft === 0, JSON.stringify(px));
  check("the avatar region does have opaque pixels (model is drawn)", px.opaque > 50, `opaque samples=${px.opaque}`);

  // ── renderer isolation (SPEC §21.13) ─────────────────────────────────
  const iso = await page.evaluate(() => ({
    req: typeof require, proc: typeof process, ipc: typeof ipcRenderer, mod: typeof module,
    keys: Object.keys(window.pagiDesktop || {}).sort(),
  }));
  check("renderer has no require/process/ipcRenderer/module", iso.req === "undefined" && iso.proc === "undefined" && iso.ipc === "undefined" && iso.mod === "undefined", JSON.stringify(iso));
  const expectKeys = ["closeNotification", "dragEnd", "dragMove", "dragStart", "getInfo", "onCaptureHotkey", "onNotificationClick", "onVisibilityChange", "openExternal", "setInteractive", "setMode", "showNotification", "startCapture"];
  check("window.pagiDesktop exposes exactly the allowed API", JSON.stringify(iso.keys) === JSON.stringify(expectKeys), JSON.stringify(iso.keys));

  // ── modes: panel button resizes the real window, Esc restores ────────
  await page.click('button[aria-label="Mở panel"]');
  await sleep(800);
  const bp = await bounds();
  check("opening the panel widens the window to 780", bp.width === 780 && bp.height === 640, `${bp.width}x${bp.height}`);
  await page.keyboard.press("Escape");
  await sleep(800);
  const bc = await bounds();
  check("closing the panel restores 360", bc.width === 360, `${bc.width}x${bc.height}`);

  // ── click-through: record what the renderer tells the shell ──────────
  await app.evaluate(({ BrowserWindow }) => {
    const w = BrowserWindow.getAllWindows()[0];
    globalThis.__ignore = [];
    const orig = w.setIgnoreMouseEvents.bind(w);
    w.setIgnoreMouseEvents = (ignore, opts) => { globalThis.__ignore.push({ ignore, forward: !!(opts && opts.forward) }); return orig(ignore, opts); };
  });
  const lastIgnore = () => app.evaluate(() => globalThis.__ignore.slice(-1)[0]);
  await page.mouse.move(6, 300); // transparent margin, left of the model
  await sleep(200);
  const overMargin = await lastIgnore();
  await page.mouse.move(180, 400); // over the model
  await sleep(200);
  const overModel = await lastIgnore();
  await page.mouse.move(150, 610); // input row
  await sleep(200);
  const overInput = await lastIgnore();
  await page.mouse.move(180, 60); // empty bubble zone
  await sleep(200);
  const overZone = await lastIgnore();
  check("over the model: window captures the mouse (ignore=false)", overModel && overModel.ignore === false, JSON.stringify(overModel));
  check("over the transparent margin: mouse passes through (ignore=true, forward)", overMargin && overMargin.ignore === true && overMargin.forward === true, JSON.stringify(overMargin));
  check("over the input row: window captures the mouse", overInput && overInput.ignore === false, JSON.stringify(overInput));
  check("over the empty bubble zone: mouse passes through", overZone && overZone.ignore === true, JSON.stringify(overZone));
  await page.mouse.move(180, 400); // settle inside the model first (this move may legitimately switch state)
  await sleep(200);
  const before = await app.evaluate(() => globalThis.__ignore.length);
  for (let i = 0; i < 20; i++) await page.mouse.move(180 + (i % 3), 400 + (i % 3)); // stays inside the model
  await sleep(200);
  const after = await app.evaluate(() => globalThis.__ignore.length);
  check("IPC only fires on a change: 20 moves inside the model => 0 calls", after - before === 0, `calls=${after - before}`);

  // ── drag: size is frozen, position follows, position is persisted ────
  const b0 = await bounds();
  await page.evaluate(() => { const d = window.pagiDesktop; d.dragStart(); d.dragMove(60, -40); d.dragMove(120, -80); d.dragEnd(); });
  await sleep(400);
  const b1 = await bounds();
  check("dragging moves the window by the delta and never changes its size", b1.x === b0.x + 120 && b1.y === b0.y - 80 && b1.width === b0.width && b1.height === b0.height, `dx=${b1.x - b0.x} dy=${b1.y - b0.y} size=${b1.width}x${b1.height}`);
  const statePath = path.join(userData, "window-state.json");
  const saved = fs.existsSync(statePath) ? JSON.parse(fs.readFileSync(statePath, "utf-8")) : null;
  check("window position is persisted after a drag", !!saved && saved.x === b1.x && saved.y === b1.y, JSON.stringify(saved));
  await page.evaluate(() => { const d = window.pagiDesktop; d.dragStart(); d.dragMove(-120, 80); d.dragEnd(); });
  await sleep(300);

  // ── hide/show: the shell reports it, and rendering stops while hidden ─
  await page.evaluate(() => {
    window.__vis = [];
    window.pagiDesktop.onVisibilityChange((v) => window.__vis.push(v));
    window.__raf = 0;
    const loop = () => { window.__raf++; requestAnimationFrame(loop); };
    loop();
  });
  const rafRate = async () => { const a = await page.evaluate(() => window.__raf); await sleep(1000); return (await page.evaluate(() => window.__raf)) - a; };
  const visibleRate = await rafRate();
  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].hide());
  await sleep(500);
  const docHiddenAfterHide = await page.evaluate(() => document.hidden);
  const hiddenRate = await rafRate();
  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].show());
  await sleep(500);
  const shownRate = await rafRate();
  const vis = await page.evaluate(() => window.__vis);
  console.log(`INFO  document.hidden after win.hide() = ${docHiddenAfterHide} (this is why the shell has to report visibility)`);
  check("the shell reports hide then show to the page (onVisibilityChange)", JSON.stringify(vis) === "[false,true]", JSON.stringify(vis));
  check("hidden window: animation frames stop", hiddenRate <= 2 && visibleRate > 10, `visible=${visibleRate}/s hidden=${hiddenRate}/s`);
  check("showing the window resumes rendering", shownRate > 10, `${shownRate}/s`);

  // ── navigation lock & window.open ────────────────────────────────────
  await page.evaluate(() => { location.href = "https://example.com/"; });
  await sleep(1500);
  check("navigating to a foreign URL is blocked", page.url().startsWith(BASE), page.url());
  await page.evaluate(() => { window.open("https://example.com/", "_blank"); });
  await sleep(800);
  check("window.open to a foreign URL opens no new window", app.windows().length === 1, `windows=${app.windows().length}`);

  // ── openExternal only allows http(s) URLs of our own origin ──────────
  await app.evaluate((electron) => {
    globalThis.__opened = [];
    electron.shell.openExternal = async (u) => { globalThis.__opened.push(u); };
  });
  await page.evaluate((base) => {
    const d = window.pagiDesktop;
    d.openExternal("file:///C:/Windows/System32/calc.exe");
    d.openExternal("http://evil.example/");
    d.openExternal("javascript:alert(1)");
    d.openExternal(base + ".evil.example/");
    d.openExternal(base + "/?session=abc");
  }, BASE);
  await sleep(500);
  const opened = await app.evaluate(() => globalThis.__opened);
  check("openExternal: only the same-origin http(s) URL goes through", opened.length === 1 && opened[0] === BASE + "/?session=abc", JSON.stringify(opened));

  // ── toast plumbing (native toasts are logged, not displayed, in this run) ──
  const toastLines = () => (fs.existsSync(toastLog) ? fs.readFileSync(toastLog, "utf-8").split("\n").filter(Boolean).map((l) => JSON.parse(l)) : []);
  await page.evaluate(() => { window.__clicks = []; window.pagiDesktop.onNotificationClick((c) => window.__clicks.push(c)); });
  await page.evaluate(() => {
    const d = window.pagiDesktop;
    const target = { type: "overlay-approval", sessionId: "s", approvalId: "ap1" };
    d.showNotification({ title: "PAGI cần bạn duyệt", body: "execute_code", kind: "approval", tag: "ap1", target });
    d.showNotification({ title: "bad kind", body: "x", kind: "malware", target: null });
    d.showNotification({ title: "big target", body: "x", kind: "cron", target: { blob: "x".repeat(2000) } });
    d.showNotification({ title: "", body: "x", kind: "cron", target: null });
    d.showNotification({ title: "PAGI cần bạn duyệt", body: "execute_code", kind: "approval", tag: "ap1", target });
  });
  await sleep(600);
  const lines = toastLines();
  check("valid toasts are raised; bad kind / oversized target / empty title are rejected", lines.length === 2 && lines.every((l) => l.title === "PAGI cần bạn duyệt" && l.kind === "approval"), JSON.stringify(lines.map((l) => l.title)));
  const mapSize = await app.evaluate(() => globalThis.__pagiToasts.size);
  check("two toasts with the same tag leave one live toast (replaced, not stacked)", mapSize === 1, `live=${mapSize}`);
  await page.evaluate(() => window.pagiDesktop.closeNotification("ap1"));
  await sleep(300);
  check("closeNotification(tag) removes that toast", (await app.evaluate(() => globalThis.__pagiToasts.has("ap1"))) === false);

  // clicking a toast: bring the hidden window back and relay {kind,target} verbatim
  await page.evaluate(() => window.pagiDesktop.showNotification({ title: "PAGI cần bạn duyệt", body: "execute_code", kind: "approval", tag: "ap9", target: { type: "overlay-approval", sessionId: "s", approvalId: "ap9" } }));
  await sleep(300);
  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].hide());
  await sleep(300);
  await app.evaluate(() => globalThis.__pagiToasts.get("ap9").emit("click"));
  await sleep(600);
  const clicked = await page.evaluate(() => window.__clicks);
  const visibleAfter = await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].isVisible());
  check("clicking a toast shows the hidden window and relays {kind,target} to the page", visibleAfter && clicked.length === 1 && clicked[0].kind === "approval" && clicked[0].target.approvalId === "ap9", JSON.stringify(clicked));

  // ── the real pipeline while HIDDEN: cron run -> /ws/notifications -> toast ──
  fs.writeFileSync(toastLog, "");
  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].hide());
  await sleep(300);
  const hiddenAtRun = (await page.evaluate(() => window.__vis.slice(-1)[0])) === false;
  const api = async (method, url, data) => page.evaluate(async ([m, u, d]) => {
    const r = await fetch(u, { method: m, credentials: "include", headers: { "Content-Type": "application/json" }, body: d ? JSON.stringify(d) : undefined });
    const t = await r.text();
    return { status: r.status, body: t ? JSON.parse(t) : null };
  }, [method, url, data]);
  const agents = (await api("GET", "/api/agents")).body;
  const agentId = (agents.find((a) => a.is_default) || agents[0]).id;
  const job = (await api("POST", "/api/cron-jobs", { agent_id: agentId, name: "e2e-hidden", schedule: "0 3 * * *", prompt: "Reply with the single word: ok" })).body;
  await api("POST", `/api/cron-jobs/${job.id}/run-now`);
  let toast = null;
  for (let i = 0; i < 120 && !toast; i++) {
    await sleep(1000);
    toast = toastLines().find((l) => /e2e-hidden/.test(l.title)) || null;
  }
  check("with the window HIDDEN, a finished cron run raises a toast via /ws/notifications", hiddenAtRun && !!toast, JSON.stringify(toast));
  check("that toast carries the result and opens the cron session on the web", !!toast && toast.kind === "cron" && toast.target.type === "web" && toast.target.path.startsWith("/?session="), JSON.stringify(toast && toast.target));
  await api("DELETE", `/api/cron-jobs/${job.id}`);
  const convs = (await api("GET", "/api/conversations")).body;
  for (const c of convs.filter((c) => (c.title || "").includes("e2e-hidden"))) await api("DELETE", `/api/conversations/${c.id}`);
  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].show());

  await page.screenshot({ path: OUT + "/electron_final.png" }).catch(() => {});

  // cleanup: overlay session(s) this run created
  const overlays = await page.evaluate(async () => { const r = await fetch("/api/conversations?origin=overlay", { credentials: "include" }); return r.json(); });
  for (const c of overlays) await page.evaluate((id) => fetch("/api/conversations/" + id, { method: "DELETE", credentials: "include" }), c.id);
  await app.close();
  try { fs.rmSync(userData, { recursive: true, force: true }); } catch {}

  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
  process.exit(failed.length ? 1 : 0);
})().catch((e) => { console.error("SCRIPT ERROR", e); process.exit(2); });
