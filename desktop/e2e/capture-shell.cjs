// e2e: capture-shell.cjs — drives a REAL backend + frontend (see e2e/README.md). Cleans up what it creates.
// Real Electron: region capture end to end. Real screen capture stays in this process's memory:
// upload + chat WebSocket are intercepted, and no screenshot of the chip is ever saved.
const { _electron } = require("playwright");
const { BASE, USER, PASSWORD, DESKTOP, ELECTRON_EXE, SHOTS } = require("./config.cjs");
const fs = require("fs"), os = require("os"), path = require("path");
const results = [];
const check = (name, ok, detail = "") => { results.push({ name, ok }); console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  — " + detail : ""}`); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const esc = async (pg) => { try { await pg.keyboard.press("Escape"); } catch { /* the window closes as it handles the key */ } };

(async () => {
  const userData = fs.mkdtempSync(path.join(os.tmpdir(), "pagi-cap-"));
  const env = { ...process.env }; delete env.ELECTRON_RUN_AS_NODE;
  const app = await _electron.launch({ executablePath: ELECTRON_EXE, args: [".", `--user-data-dir=${userData}`], cwd: DESKTOP, env });
  const page = await app.firstWindow();

  // intercept: the upload is mocked; user_message frames are logged in the page and DROPPED,
  // so neither the screenshot nor the prompt ever reaches the backend / an LLM.
  const uploads = [];
  await page.route("**/api/conversations/*/files", async (route) => {
    uploads.push({ png: (route.request().postData() || "").includes("image/png"), bytes: (route.request().postDataBuffer() || Buffer.alloc(0)).length });
    await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({ id: "attX", kind: "image", filename: "screenshot.png", content_type: "image/png", size_bytes: 1 }) });
  });
  const installSendTap = () => page.addInitScript(() => {
    window.__sent = [];
    const orig = WebSocket.prototype.send;
    WebSocket.prototype.send = function (data) {
      try { const m = JSON.parse(data); if (m.type === "user_message") { window.__sent.push(m); return; } } catch {}
      return orig.call(this, data);
    };
  });
  await installSendTap();
  await page.reload();

  await page.waitForSelector('input[type="password"]', { timeout: 30000 });
  await page.fill('input[type="password"]', PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForFunction(() => document.querySelector("textarea") && !document.querySelector("textarea").disabled, null, { timeout: 30000 });
  await page.waitForFunction(() => document.querySelector("[data-avatar-status]")?.dataset.avatarStatus === "ready", null, { timeout: 60000 });

  const displays = await app.evaluate(({ screen }) => screen.getAllDisplays().map((d) => ({ id: d.id, bounds: d.bounds, sf: d.scaleFactor, primary: d.id === screen.getPrimaryDisplay().id })));
  const overlayVisible = () => app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().find((w) => /\/overlay/.test(w.webContents.getURL())).isVisible());
  const selectors = () => app.windows().filter((w) => /selector\.html/.test(w.url()));
  const waitSelectors = async (n) => { for (let i = 0; i < 100 && selectors().length < n; i++) await sleep(100); await sleep(400); return selectors(); };
  const chip = () => page.locator('img[alt="Ảnh chụp đang chờ gửi"]');
  const btn = 'button[aria-label="Chụp vùng màn hình"]';
  const primaryPage = async () => {
    const list = selectors();
    const primary = displays.find((d) => d.primary);
    for (const w of list) {
      const pos = await w.evaluate(() => ({ x: window.screenX, y: window.screenY }));
      if (Math.abs(pos.x - primary.bounds.x) <= 2 && Math.abs(pos.y - primary.bounds.y) <= 2) return w;
    }
    return list[0];
  };
  console.log(`INFO  displays: ${displays.map((d) => `${d.bounds.width}x${d.bounds.height}@${d.sf}`).join(", ")}`);

  // ── start via the button: overlay hides, one selector per display ─────
  await page.click(btn);
  const sels = await waitSelectors(displays.length);
  check("a selector window opens on every display", sels.length === displays.length, `selectors=${sels.length} displays=${displays.length}`);
  check("the overlay hides itself so it can't appear in the picture", (await overlayVisible()) === false);
  const sp = await primaryPage();
  const info = await sp.evaluate(() => ({ req: typeof require, proc: typeof process, ipc: typeof ipcRenderer, keys: Object.keys(window.pagiSelector || {}).sort(), img: document.getElementById("shot").naturalWidth, csp: !!document.querySelector('meta[http-equiv="Content-Security-Policy"]') }));
  check("selector shows the frozen screenshot and is isolated (no require/process/ipcRenderer)", info.img > 0 && info.req === "undefined" && info.proc === "undefined" && info.ipc === "undefined" && JSON.stringify(info.keys) === '["done","onImage"]', JSON.stringify(info));
  const selWinProps = await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().filter((w) => /selector\.html/.test(w.webContents.getURL())).map((w) => ({ top: w.isAlwaysOnTop(), b: w.getBounds() })));
  check("selector windows are topmost and cover their display exactly", selWinProps.every((w) => w.top) && displays.every((d) => selWinProps.some((w) => w.b.x === d.bounds.x && w.b.y === d.bounds.y && w.b.width === d.bounds.width && w.b.height === d.bounds.height)), JSON.stringify(selWinProps.map((w) => w.b)));

  // a second gesture while one is running is ignored
  const second = await page.evaluate(() => window.pagiDesktop.startCapture());
  check("a second capture while one is active is ignored (returns null)", second === null);

  // ── Esc cancels: everything is restored, no chip ──────────────────────
  await esc(sp);
  for (let i = 0; i < 50 && selectors().length > 0; i++) await sleep(100);
  await sleep(300);
  check("Esc cancels: selector windows close, overlay is back, no chip", selectors().length === 0 && (await overlayVisible()) && (await chip().count()) === 0);

  // ── a tiny drag is a mis-click, refused ───────────────────────────────
  await page.click(btn);
  await waitSelectors(displays.length);
  const sp2 = await primaryPage();
  await sp2.mouse.move(200, 200); await sp2.mouse.down(); await sp2.mouse.move(203, 202); await sp2.mouse.up().catch(() => {});
  for (let i = 0; i < 50 && selectors().length > 0; i++) await sleep(100);
  await sleep(300);
  check("a region smaller than 8px is refused (no chip)", (await chip().count()) === 0 && (await overlayVisible()));

  // ── right-click cancels ───────────────────────────────────────────────
  await page.click(btn);
  await waitSelectors(displays.length);
  const sp3 = await primaryPage();
  await sp3.mouse.click(300, 300, { button: "right" }).catch(() => {});
  for (let i = 0; i < 50 && selectors().length > 0; i++) await sleep(100);
  await sleep(300);
  check("right-click cancels", selectors().length === 0 && (await chip().count()) === 0);

  // ── a real region ─────────────────────────────────────────────────────
  await page.click(btn);
  await waitSelectors(displays.length);
  const sp4 = await primaryPage();
  await sp4.mouse.move(100, 100); await sp4.mouse.down(); await sp4.mouse.move(250, 180); await sp4.mouse.move(400, 300); await sp4.mouse.up().catch(() => {});
  await chip().waitFor({ timeout: 15000 });
  await sleep(500);
  const dims = await chip().evaluate((img) => ({ w: img.naturalWidth, h: img.naturalHeight }));
  const prim = displays.find((d) => d.primary);
  const aspectOk = Math.abs(dims.w / dims.h - 300 / 200) < 0.03;
  const isMaxDisplay = displays.every((d) => d.bounds.width * d.sf <= prim.bounds.width * prim.sf + 1);
  const sizeOk = !isMaxDisplay || (Math.abs(dims.w - 300 * prim.sf) <= 3 && Math.abs(dims.h - 200 * prim.sf) <= 3);
  check("the 300x200 CSS-px region comes back as a PNG with the right aspect and physical size", aspectOk && sizeOk, `chip=${dims.w}x${dims.h} (scale ${prim.sf})`);
  check("after a capture the selectors are closed and the overlay is visible again", selectors().length === 0 && (await overlayVisible()));
  check("nothing was uploaded by capturing", uploads.length === 0);

  // ── send it (upload + chat socket are intercepted) ────────────────────
  await page.locator("textarea").fill("đây là gì?");
  await page.click('button[aria-label="Gửi"]');
  for (let i = 0; i < 50 && (await page.evaluate(() => window.__sent.length)) === 0; i++) await sleep(100);
  const um = (await page.evaluate(() => window.__sent))[0];
  check("sending uploads a real PNG once and attaches it to the message", uploads.length === 1 && uploads[0].png && uploads[0].bytes > 1000 && um && JSON.stringify(um.attachment_ids) === '["attX"]', JSON.stringify({ up: uploads[0], um }));
  check("the chip is cleared after sending", (await chip().count()) === 0);

  // ── hotkey relay while the overlay is HIDDEN ──────────────────────────
  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().find((w) => /\/overlay/.test(w.webContents.getURL())).hide());
  await sleep(300);
  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().find((w) => /\/overlay/.test(w.webContents.getURL())).webContents.send("overlay:capture-hotkey"));
  await waitSelectors(displays.length);
  check("the capture hotkey works even while the overlay is hidden", selectors().length === displays.length);
  await esc(await primaryPage());
  for (let i = 0; i < 50 && selectors().length > 0; i++) await sleep(100);
  await sleep(300);
  check("after cancelling a hotkey capture the overlay is shown again", (await overlayVisible()) === true);

  // ── nothing captures on its own ───────────────────────────────────────
  await sleep(10000);
  check("10s of idling: no selector window ever appeared by itself", selectors().length === 0);

  const overlays = await page.evaluate(async () => (await fetch("/api/conversations?origin=overlay", { credentials: "include" })).json());
  for (const c of overlays) await page.evaluate((id) => fetch("/api/conversations/" + id, { method: "DELETE", credentials: "include" }), c.id);
  await app.close();
  try { fs.rmSync(userData, { recursive: true, force: true }); } catch {}
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
  process.exit(failed.length ? 1 : 0);
})().catch((e) => { console.error("SCRIPT ERROR", e); process.exit(2); });
