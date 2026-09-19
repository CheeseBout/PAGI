// e2e: capture-ui.cjs — drives a REAL backend + frontend (see e2e/README.md). Cleans up what it creates.
// Capture flow in the overlay page: stubbed startCapture, upload intercepted (nothing leaves the machine).
const { chromium } = require("playwright");
const { BASE, USER, PASSWORD, DESKTOP, ELECTRON_EXE, SHOTS } = require("./config.cjs");
const results = [];
const check = (name, ok, detail = "") => { results.push({ name, ok }); console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  — " + detail : ""}`); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const browser = await chromium.launch({ args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader"] });
  const ctx = await browser.newContext({ viewport: { width: 360, height: 640 } });
  await ctx.addInitScript(() => {
    window.__captureCalls = 0;
    window.__nextCapture = "png"; // "png" | "cancel"
    window.__hotkey = null;
    const makePng = async () => {
      const c = new OffscreenCanvas(40, 30);
      const g = c.getContext("2d");
      g.fillStyle = "#7c9cff"; g.fillRect(0, 0, 40, 30);
      return await (await c.convertToBlob({ type: "image/png" })).arrayBuffer();
    };
    window.pagiDesktop = {
      setMode() {}, setInteractive() {}, dragStart() {}, dragMove() {}, dragEnd() {},
      openExternal() {}, showNotification() {}, closeNotification() {},
      onNotificationClick() { return () => {}; }, onVisibilityChange() { return () => {}; },
      onCaptureHotkey(cb) { window.__hotkey = cb; return () => {}; },
      startCapture: async () => { window.__captureCalls++; return window.__nextCapture === "cancel" ? null : await makePng(); },
      getInfo: async () => ({ version: "t", platform: "t" }),
    };
  });
  const page = await ctx.newPage();

  let chatWs = null;
  const fromPage = [];
  await page.routeWebSocket(/\/ws\/chat\//, (ws) => { chatWs = ws; ws.onMessage((m) => fromPage.push(JSON.parse(m))); });
  await page.routeWebSocket(/\/ws\/notifications/, (ws) => ws.send(JSON.stringify({ type: "hello", pending_approvals: 0 })));
  const uploads = [];
  await page.route("**/api/conversations/*/files", async (route) => {
    const req = route.request();
    uploads.push({ ct: req.headers()["content-type"] || "", bytes: (req.postDataBuffer() || Buffer.alloc(0)).length, hasPng: (req.postData() || "").includes("image/png") });
    await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({ id: "att1", kind: "image", filename: "screenshot.png", content_type: "image/png", size_bytes: 123 }) });
  });
  let visionOff = false;
  await page.route("**/api/agents", async (route) => {
    const res = await route.fetch();
    const list = await res.json();
    if (visionOff) list.forEach((a) => { a.vision_enabled = false; });
    await route.fulfill({ response: res, json: list });
  });

  const done = () => chatWs.send(JSON.stringify({ type: "message_done", message_id: "m" + Math.random(), tokens_in: 1, tokens_out: 1, cached_tokens: 0, cost_usd: 0 }));
  const ready = async () => {
    await page.goto(BASE + "/overlay");
    await page.waitForSelector('input[type="password"]', { timeout: 15000 }).then(async () => {
      await page.fill('input[type="password"]', PASSWORD); await page.click('button[type="submit"]');
    }).catch(() => {});
    await page.waitForFunction(() => document.querySelector("textarea") && !document.querySelector("textarea").disabled, null, { timeout: 20000 });
    for (let i = 0; i < 50 && !chatWs; i++) await sleep(100);
  };
  const chip = () => page.locator('img[alt="Ảnh chụp đang chờ gửi"]');
  const ta = () => page.locator("textarea");
  const calls = () => page.evaluate(() => window.__captureCalls);

  await ready();

  // ── privacy invariant: nothing captures by itself ─────────────────────
  await sleep(12000);
  check("idle for 12s: startCapture was never called (no timer/automatic capture)", (await calls()) === 0);

  // ── cancel: no chip, no upload ───────────────────────────────────────
  await page.evaluate(() => { window.__nextCapture = "cancel"; });
  await page.click('button[aria-label="Chụp vùng màn hình"]');
  await sleep(400);
  check("cancelling a capture leaves no chip and uploads nothing", (await chip().count()) === 0 && uploads.length === 0, `calls=${await calls()}`);

  // ── capture via the button: chip shown, NOT uploaded yet ─────────────
  await page.evaluate(() => { window.__nextCapture = "png"; });
  await page.click('button[aria-label="Chụp vùng màn hình"]');
  await chip().waitFor();
  await sleep(500);
  check("capture button: preview chip appears", (await chip().count()) === 1);
  check("the image is NOT uploaded until the user sends it", uploads.length === 0);

  // ── remove with ✕ ────────────────────────────────────────────────────
  await page.click('button[aria-label="Bỏ ảnh chụp"]');
  await sleep(200);
  check("✕ removes the chip and still nothing is uploaded", (await chip().count()) === 0 && uploads.length === 0);

  // ── /screen with a question: captures, does not send ─────────────────
  fromPage.length = 0;
  await ta().fill("/screen giải thích lỗi này");
  await ta().press("Enter");
  await chip().waitFor();
  await sleep(300);
  check("/screen <question> starts a capture, keeps the question in the box, sends nothing yet",
    (await ta().inputValue()) === "giải thích lỗi này" && fromPage.filter((m) => m.type === "user_message").length === 0 && uploads.length === 0,
    JSON.stringify(await ta().inputValue()));

  // ── send: upload happens now, message carries the attachment id ──────
  await ta().press("Enter");
  for (let i = 0; i < 30 && !fromPage.some((m) => m.type === "user_message"); i++) await sleep(100);
  const msg = fromPage.find((m) => m.type === "user_message");
  check("sending uploads the PNG exactly once (multipart)", uploads.length === 1 && uploads[0].hasPng && /multipart\/form-data/.test(uploads[0].ct), JSON.stringify(uploads[0]));
  check("the message carries content + attachment_ids", msg && msg.content === "giải thích lỗi này" && JSON.stringify(msg.attachment_ids) === '["att1"]', JSON.stringify(msg));
  check("after sending, the chip and the text box are cleared", (await chip().count()) === 0 && (await ta().inputValue()) === "");
  done(); await sleep(300);

  // ── screenshot alone (no text) is a valid message ────────────────────
  fromPage.length = 0; uploads.length = 0;
  await page.click('button[aria-label="Chụp vùng màn hình"]');
  await chip().waitFor();
  await page.click('button[aria-label="Gửi"]');
  for (let i = 0; i < 30 && !fromPage.some((m) => m.type === "user_message"); i++) await sleep(100);
  const m2 = fromPage.find((m) => m.type === "user_message");
  check("a screenshot with no text can be sent", m2 && m2.content === "" && m2.attachment_ids?.length === 1, JSON.stringify(m2));
  done(); await sleep(300);

  // ── second capture replaces the first, with a notice ─────────────────
  await page.click('button[aria-label="Chụp vùng màn hình"]'); await chip().waitFor();
  await page.waitForFunction(() => !document.querySelector('button[aria-label="Chụp vùng màn hình"]').disabled); // the first capture has fully finished
  await page.click('button[aria-label="Chụp vùng màn hình"]');
  await sleep(100);
  // the second capture is asynchronous (the stub encodes a PNG): wait until it has fully finished
  await page.waitForFunction(() => !document.querySelector('button[aria-label="Chụp vùng màn hình"]').disabled);
  await sleep(200);
  check("capturing again keeps ONE chip and says the old one was replaced", (await chip().count()) === 1 && (await page.locator("text=Đã thay ảnh chụp trước đó").count()) === 1);
  await page.click('button[aria-label="Bỏ ảnh chụp"]');

  // ── the global hotkey relays to the same flow ────────────────────────
  await page.waitForFunction(() => !document.querySelector('button[aria-label="Chụp vùng màn hình"]').disabled);
  const c0 = await calls();
  await page.evaluate(() => window.__hotkey());
  await chip().waitFor();
  check("hotkey relay starts a capture through the same path", (await calls()) === c0 + 1);
  await page.click('button[aria-label="Bỏ ảnh chụp"]');

  // ── upload failure surfaces an error and keeps the image ─────────────
  await page.unroute("**/api/conversations/*/files");
  await page.route("**/api/conversations/*/files", (r) => r.fulfill({ status: 400, contentType: "application/json", body: JSON.stringify({ error: { code: "too_large", message: "File exceeds the 20 MB limit" } }) }));
  await page.click('button[aria-label="Chụp vùng màn hình"]'); await chip().waitFor();
  fromPage.length = 0;
  await page.click('button[aria-label="Gửi"]');
  await page.waitForSelector("text=File exceeds the 20 MB limit", { timeout: 5000 });
  check("a failed upload shows the server's error, keeps the screenshot, and sends no message", (await chip().count()) === 1 && fromPage.filter((m) => m.type === "user_message").length === 0);
  await page.click('button[aria-label="Bỏ ảnh chụp"]');

  // ── vision disabled: button off, /screen explains and captures nothing ─
  visionOff = true;
  await page.reload();
  await page.waitForFunction(() => document.querySelector("textarea") && !document.querySelector("textarea").disabled, null, { timeout: 20000 });
  const before = await calls();
  const btn = page.locator('button[aria-label="Chụp vùng màn hình"]');
  check("agent without vision: capture button is disabled with a reason", (await btn.isDisabled()) && /vision/i.test((await btn.getAttribute("title")) || ""), await btn.getAttribute("title"));
  await ta().fill("/screen");
  await ta().press("Enter");
  await sleep(500);
  check("agent without vision: /screen explains and captures nothing", (await calls()) === before && (await page.locator("text=chưa bật vision").count()) >= 1);

  // cleanup
  const list = await (await page.request.get(BASE + "/api/conversations?origin=overlay")).json();
  for (const c of list) await page.request.delete(BASE + "/api/conversations/" + c.id);
  await browser.close();
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
  process.exit(failed.length ? 1 : 0);
})().catch((e) => { console.error("SCRIPT ERROR", e); process.exit(2); });
