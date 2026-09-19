// e2e: ui.cjs — drives a REAL backend + frontend (see e2e/README.md). Cleans up what it creates.
// Deterministic UI verification of the overlay: real backend for REST, mocked chat/notification WebSockets.
const { chromium } = require("playwright");
const { BASE, USER, PASSWORD, DESKTOP, ELECTRON_EXE, SHOTS } = require("./config.cjs");
const OUT = SHOTS;

const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  — " + detail : ""}`);
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const browser = await chromium.launch({ args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader"] });
  const ctx = await browser.newContext({ viewport: { width: 360, height: 640 } });
  const page = await ctx.newPage();

  // ── mock the two WebSockets ─────────────────────────────────────────
  let chatWs = null;
  const fromPage = [];
  await page.routeWebSocket(/\/ws\/chat\//, (ws) => {
    chatWs = ws;
    ws.onMessage((m) => fromPage.push(JSON.parse(m)));
  });
  await page.routeWebSocket(/\/ws\/notifications/, (ws) => ws.send(JSON.stringify({ type: "hello", pending_approvals: 0 })));
  const push = (ev) => chatWs.send(JSON.stringify(ev));
  const done = (text, id = "m" + Math.random()) => {
    if (text) push({ type: "token", message_id: id, content: text });
    push({ type: "message_done", message_id: id, tokens_in: 1, tokens_out: 1, cached_tokens: 0, cost_usd: 0 });
  };

  // ── login, wait for the session ─────────────────────────────────────
  await page.goto(BASE + "/overlay");
  await page.waitForSelector('input[type="password"]');
  await page.fill('input[type="password"]', PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForFunction(() => document.querySelector("textarea") && !document.querySelector("textarea").disabled);
  await page.waitForFunction(() => document.querySelector("[data-avatar-status]")?.dataset.avatarStatus === "ready", null, { timeout: 30000 });
  for (let i = 0; i < 50 && !chatWs; i++) await sleep(100);
  check("chat socket opened for the overlay session", !!chatWs);

  const bubble = page.locator('[role="alertdialog"], [data-overlay-hit]:has(> .bubble-md), [data-overlay-hit].rounded-2xl.border').first();
  const bubbleBox = () => page.locator("div.rounded-2xl.border.bg-bg-elev.p-3").first();
  const rect = (loc) => loc.evaluate((e) => { const r = e.getBoundingClientRect(); return { top: r.top, bottom: r.bottom, left: r.left, right: r.right, h: r.height }; });

  // ── 1. short reply: shown whole, and NOT on top of the model ─────────
  done("Xin chào! Mình sẵn sàng giúp bạn.");
  await page.waitForSelector("text=Xin chào! Mình sẵn sàng giúp bạn.");
  const modelRect = await page.locator("[data-overlay-model]").evaluate((e) => { const r = e.getBoundingClientRect(); return { top: r.top, bottom: r.bottom }; });
  const b1 = await rect(bubbleBox());
  check("short reply appears whole in the bubble", true);
  check("bubble sits above the model, not over it", b1.bottom <= modelRect.top + 1, `bubble.bottom=${Math.round(b1.bottom)} model.top=${Math.round(modelRect.top)}`);
  check("short reply does not open the panel", (await page.locator("section").count()) === 0);
  await page.screenshot({ path: OUT + "/ui1_short.png" });

  // ── 2. TTL: visible at ~5s, gone by ~10s; hover pauses the countdown ──
  await sleep(4500);
  check("bubble still visible after ~5s", await page.locator("text=Xin chào! Mình sẵn sàng giúp bạn.").isVisible());
  await page.locator("text=Xin chào! Mình sẵn sàng giúp bạn.").hover();
  await sleep(6000); // would be gone by now without the hover
  check("hovering pauses the fade-out", await page.locator("text=Xin chào! Mình sẵn sàng giúp bạn.").isVisible());
  await page.mouse.move(5, 5);
  await sleep(9500);
  check("bubble fades after the TTL once the mouse leaves", !(await page.locator("text=Xin chào! Mình sẵn sàng giúp bạn.").count()));

  // ── 3. long reply: truncated, hint shown, panel NOT auto-opened ───────
  const long = "Đây là một câu trả lời rất dài. ".repeat(30);
  done(long);
  await page.waitForSelector("text=bấm để xem đầy đủ");
  const shownLen = (await bubbleBox().innerText()).length;
  check("long reply is cut and shows the 'click to view' hint", shownLen < long.length && shownLen < 400, `shown≈${shownLen} chars of ${long.length}`);
  check("long reply does not auto-open the panel", (await page.locator("section").count()) === 0);
  await page.screenshot({ path: OUT + "/ui2_long.png" });

  // ── 4. click the bubble -> panel opens ───────────────────────────────
  await bubbleBox().click();
  await page.waitForSelector("section");
  check("clicking the bubble opens the panel", (await page.locator("section").count()) === 1);
  await page.keyboard.press("Escape");
  await sleep(300);
  check("Esc closes the panel", (await page.locator("section").count()) === 0);
  await sleep(9000); // let this bubble expire

  // ── 5. code block: text before the fence only ────────────────────────
  done("Đây là ví dụ:\n```python\nprint('hi')\n```\nXong.");
  await page.waitForSelector("text=bấm để xem đầy đủ");
  const codeTxt = await bubbleBox().innerText();
  check("code block is never rendered inside the bubble", !codeTxt.includes("print(") && !codeTxt.includes("```") && codeTxt.includes("Đây là ví dụ:"), JSON.stringify(codeTxt.slice(0, 60)));
  await sleep(9000);

  // ── 6. tool label, without arguments ─────────────────────────────────
  push({ type: "tool_call_start", tool_call_id: "t1", tool_name: "write_file", args: { path: "secret.txt", content: "TOPSECRET" } });
  await page.waitForSelector("text=Đang dùng write_file…");
  const toolTxt = await bubbleBox().innerText();
  check("tool label shows the tool name only", toolTxt.includes("write_file") && !toolTxt.includes("TOPSECRET") && !toolTxt.includes("secret.txt"), JSON.stringify(toolTxt));
  push({ type: "tool_call_result", tool_call_id: "t1", result: {} });
  await sleep(300);

  // ── 7. approval card: persistent, decides over the chat socket ───────
  push({ type: "approval_required", approval_id: "ap1", tool_call_id: "t2", tool_name: "execute_code", args: { language: "python", code: "print(1)" } });
  await page.waitForSelector('[role="alertdialog"]');
  const cardTxt = await page.locator('[role="alertdialog"]').innerText();
  check("approval card shows tool + args + both buttons", cardTxt.includes("execute_code") && cardTxt.includes("print(1)") && cardTxt.includes("Duyệt") && cardTxt.includes("Từ chối"));
  check("approval of a risky tool is flagged as irreversible", cardTxt.includes("không hoàn tác"));
  await page.screenshot({ path: OUT + "/ui3_approval.png" });
  await sleep(11000); // longer than the bubble TTL
  check("approval card never auto-hides", await page.locator('[role="alertdialog"]').isVisible());

  // an open panel still lets the approval card through
  await page.click('button[aria-label="Mở panel"]');
  await page.waitForSelector("section");
  check("approval card stays visible while the panel is open", await page.locator('[role="alertdialog"]').isVisible());
  await page.keyboard.press("Escape");
  await sleep(300);

  fromPage.length = 0;
  await page.locator("button", { hasText: "Duyệt" }).click();
  await sleep(300);
  const decision = fromPage.find((m) => m.type === "approval_decision");
  check("clicking Duyệt sends approval_decision(approve) for that approval", decision?.decision === "approve" && decision?.approval_id === "ap1", JSON.stringify(decision));
  check("the card disappears after deciding", (await page.locator('[role="alertdialog"]').count()) === 0);
  check("no 'remember for session' option is offered (v1)", decision && decision.remember === undefined);

  // a second approval -> deny
  push({ type: "approval_required", approval_id: "ap2", tool_call_id: "t3", tool_name: "write_file", args: { path: "a.txt" } });
  await page.waitForSelector('[role="alertdialog"]');
  fromPage.length = 0;
  await page.locator("button", { hasText: "Từ chối" }).click();
  await sleep(300);
  check("clicking Từ chối sends approval_decision(deny)", fromPage.find((m) => m.type === "approval_decision")?.decision === "deny");

  // approval resolved elsewhere (web) -> card closes by itself
  push({ type: "approval_required", approval_id: "ap3", tool_call_id: "t4", tool_name: "write_file", args: {} });
  await page.waitForSelector('[role="alertdialog"]');
  push({ type: "approval_resolved", approval_id: "ap3", status: "approved" });
  await sleep(300);
  check("a decision made elsewhere closes the card", (await page.locator('[role="alertdialog"]').count()) === 0);

  // ── 7b. regression: a bubble removed from under the cursor must not freeze later fades
  push({ type: "approval_required", approval_id: "ap4", tool_call_id: "t5", tool_name: "write_file", args: {} });
  await page.waitForSelector('[role="alertdialog"]');
  await page.locator('[role="alertdialog"]').hover();
  push({ type: "approval_resolved", approval_id: "ap4", status: "approved" });
  await sleep(300);
  await page.mouse.move(5, 5); // cursor leaves; the old bubble is already gone, so no mouseleave ever fired
  done("Tin nhắn sau khi bong bóng bị gỡ dưới con trỏ");
  await page.waitForSelector("text=Tin nhắn sau khi bong bóng bị gỡ dưới con trỏ");
  await sleep(10000);
  check("fade-out still works after a bubble vanished under the cursor", (await page.locator("text=Tin nhắn sau khi bong bóng bị gỡ dưới con trỏ").count()) === 0);
  await page.mouse.move(5, 5);

  // ── 8. error bubble fades ────────────────────────────────────────────
  push({ type: "error", code: "provider_error", message: "Provider exploded" });
  await page.waitForSelector("text=Provider exploded");
  check("error bubble appears", true);
  await sleep(7500);
  check("error bubble fades on its own", (await page.locator("text=Provider exploded").count()) === 0);

  // ── 9. page never scrolls (regression for the sr-only overflow) ──────
  await page.click('button[aria-label="Mở panel"]');
  await page.waitForSelector("section");
  await sleep(500);
  const dims = await page.evaluate(() => ({ h: document.documentElement.scrollHeight, vh: innerHeight, w: document.documentElement.scrollWidth, vw: innerWidth }));
  check("document is not taller/wider than the window with the panel open", dims.h <= dims.vh + 1 && dims.w <= dims.vw + 1, JSON.stringify(dims));
  await page.screenshot({ path: OUT + "/ui4_panel.png" });

  // ── cleanup: overlay session created by this run ─────────────────────
  const list = await (await page.request.get(BASE + "/api/conversations?origin=overlay")).json();
  for (const c of list) await page.request.delete(BASE + "/api/conversations/" + c.id);
  await browser.close();

  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
  process.exit(failed.length ? 1 : 0);
})().catch((e) => { console.error("SCRIPT ERROR", e); process.exit(2); });
