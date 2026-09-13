/**
 * Full-feature E2E scenario (drives the real UI + real backend + real OpenAI).
 * Run: `npm run test:e2e -- scenario.spec.ts`  (3 services must be up).
 * Results are summarised into ../../RESULT.md by the reporter-less runner script.
 */
import { test, expect, Page } from "@playwright/test";
import * as fs from "fs";

const SHOTS = "e2e-screenshots";
fs.mkdirSync(SHOTS, { recursive: true });
const shot = (p: Page, n: string) => p.screenshot({ path: `${SHOTS}/scn-${n}.png`, fullPage: true }).catch(() => {});

async function login(page: Page) {
  await page.goto("/");
  if (!/\/login$/.test(page.url())) return;
  await expect(page.locator("form.login-card")).toBeVisible();
  await page.locator('input[type="password"]').fill("admin");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.locator(".app-shell")).toBeVisible({ timeout: 15_000 });
}

/** authed fetch from inside the page (carries the session cookie) */
async function api<T = any>(page: Page, method: string, path: string, body?: any): Promise<{ status: number; json: T }> {
  return page.evaluate(
    async ([m, p, b]) => {
      const r = await fetch("/api" + p, {
        method: m as string,
        headers: b ? { "Content-Type": "application/json" } : undefined,
        body: b ? JSON.stringify(b) : undefined,
        credentials: "include",
      });
      let j: any = null;
      try { j = await r.json(); } catch { /* 204 */ }
      return { status: r.status, json: j };
    },
    [method, path, body] as const,
  );
}

async function newChatWith(page: Page, providerText: string): Promise<string> {
  const picker = page.locator("select.model-picker");
  const opt = picker.locator("option", { hasText: providerText });
  await picker.selectOption((await opt.getAttribute("value")) as string);
  await page.getByRole("button", { name: "+ New chat" }).click();
  await expect(page.locator(".ws-dot.on")).toBeVisible({ timeout: 15_000 });
  const convs = (await api(page, "GET", "/conversations")).json;
  return convs[0].id; // newest
}

async function send(page: Page, text: string) {
  await page.locator(".input-box textarea").fill(text);
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.locator(".chat-window .msg-user").last()).toContainText(text.slice(0, 20), { timeout: 8_000 });
}

/** Wait (via the API, WS-render-timing-independent) for the turn's final
 *  assistant text, then confirm the UI shows an assistant bubble. */
async function assistantReply(page: Page, convId: string, timeout = 60_000): Promise<string> {
  let text = "";
  await expect(async () => {
    const d = (await api(page, "GET", `/conversations/${convId}`)).json;
    const msgs = d.messages || [];
    // last assistant message that carries content (not just a tool_call stub)
    for (let i = msgs.length - 1; i >= 0; i--) {
      if (msgs[i].role === "assistant" && (msgs[i].content || "").trim()) { text = msgs[i].content.trim(); break; }
    }
    expect(text.length).toBeGreaterThan(0);
  }).toPass({ timeout, intervals: [1500] });
  await expect(page.locator(".chat-window .msg.msg-assistant .msg-body").last()).toBeVisible({ timeout: 15_000 });
  return text;
}

// ─────────────────────────────────────────────────────────────────────────────
test.describe("PAGI — full feature scenario", () => {
  // ── A. Auth & shell ──────────────────────────────────────────────
  test("A1 login → shell + sidebar + 4-agent model picker", async ({ page }) => {
    await login(page);
    await expect(page.locator(".sidebar")).toBeVisible();
    await expect(page.getByRole("button", { name: "+ New chat" })).toBeVisible();
    const opts = page.locator("select.model-picker option");
    await expect(opts).toHaveCount(4);
    const txt = (await opts.allTextContents()).join(" | ").toLowerCase();
    for (const p of ["openai", "anthropic", "gemini", "openrouter"]) expect(txt).toContain(p);
    await shot(page, "A1-shell");
  });

  test("A2 wrong password → error banner", async ({ page }) => {
    await page.goto("/");
    await page.evaluate(() => fetch("/api/auth/logout", { method: "POST", credentials: "include" }));
    await page.goto("/");
    await page.locator('input[type="password"]').fill("nope");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.locator(".error-banner")).toContainText(/wrong|sai/i);
  });

  // ── B. Chat (real OpenAI) ───────────────────────────────────────
  test("B1 streaming answer + markdown code block + auto title", async ({ page }) => {
    test.setTimeout(120_000);
    await login(page);
    const cid = await newChatWith(page, "openai");
    await send(page, "Viet ham Python mot dong tinh binh phuong. Chi tra ve code trong khoi markdown.");
    const reply = await assistantReply(page, cid);
    expect(reply.length).toBeGreaterThan(0);
    await expect(page.locator(".msg.msg-assistant pre code, .msg.msg-assistant code").first())
      .toBeVisible({ timeout: 10_000 });
    await expect(page.locator(".conv-item.active .conv-title")).not.toHaveText("New chat", { timeout: 15_000 });
    await shot(page, "B1-stream");
  });

  test("B2 Anthropic agent (no key) → provider_error inline banner", async ({ page }) => {
    await login(page);
    await newChatWith(page, "anthropic");
    await send(page, "hi");
    await expect(page.locator(".error-banner.inline")).toBeVisible({ timeout: 25_000 });
    await shot(page, "B2-provider-error");
  });

  test("B3 regenerate last answer", async ({ page }) => {
    test.setTimeout(150_000);
    await login(page);
    const cid = await newChatWith(page, "openai");
    await send(page, "Tra loi dung mot tu: thu do nuoc Phap.");
    await assistantReply(page, cid);
    const btn = page.getByRole("button", { name: /Regenerate/i });
    await expect(btn).toBeVisible({ timeout: 15_000 });
    const before = (await api(page, "GET", `/conversations/${cid}`)).json.messages.length;
    await btn.click();
    await expect(async () => {
      const d = (await api(page, "GET", `/conversations/${cid}`)).json;
      const asst = d.messages.filter((m: any) => m.role === "assistant" && (m.content || "").trim());
      expect(asst.length).toBeGreaterThan(0);
      expect(d.messages.length).toBeLessThanOrEqual(before); // truncated then re-ran
    }).toPass({ timeout: 90_000, intervals: [2000] });
    await shot(page, "B3-regenerate");
  });

  test("B4 rename + delete conversation", async ({ page }) => {
    await login(page);
    await page.getByRole("button", { name: "+ New chat" }).click();
    await expect(page.locator(".conv-item.active")).toHaveCount(1);
    await page.locator(".conv-item.active .conv-del").click();
    await expect(page.getByRole("button", { name: "+ Start a new chat" })).toBeVisible();
  });

  // ── C. Tools + HITL ─────────────────────────────────────────────
  test("C1 get_current_datetime (auto tool)", async ({ page }) => {
    test.setTimeout(120_000);
    await login(page);
    const cid = await newChatWith(page, "openai");
    await send(page, "Dung cong cu de cho toi biet ngay gio hien tai.");
    await expect(page.locator(".tool-event")).toContainText(/get_current_datetime/i, { timeout: 40_000 });
    const reply = await assistantReply(page, cid);
    expect(reply).toMatch(/20\d\d|:\d\d|\d{1,2}h/);
    await shot(page, "C1-datetime");
  });

  test("C2 execute_code → ApprovalCard → Approve → result", async ({ page }) => {
    test.setTimeout(120_000);
    await login(page);
    const cid = await newChatWith(page, "openai");
    await send(page, "Chay Python: print(6*7). Dung execute_code.");
    const card = page.locator(".approval-card");
    await expect(card).toBeVisible({ timeout: 40_000 });
    await expect(card.locator(".approval-code")).toContainText("6*7");
    await shot(page, "C2a-approval");
    await card.getByRole("button", { name: /^Approve$/ }).click();
    const reply = await assistantReply(page, cid, 90_000);
    expect(reply).toContain("42");
    await shot(page, "C2b-approved");
  });

  test("C3 execute_code → Deny → agent acknowledges", async ({ page }) => {
    test.setTimeout(120_000);
    await login(page);
    const cid = await newChatWith(page, "openai");
    await send(page, "Chay Python: print('secret'). Dung execute_code.");
    const card = page.locator(".approval-card");
    await expect(card).toBeVisible({ timeout: 40_000 });
    await card.getByRole("button", { name: /Deny/ }).click();
    // the tool result must be user_denied
    await expect(async () => {
      const d = (await api(page, "GET", `/conversations/${cid}`)).json;
      const toolMsg = d.messages.find((m: any) => m.role === "tool" && /user_denied/.test(m.content || ""));
      expect(toolMsg).toBeTruthy();
    }).toPass({ timeout: 60_000, intervals: [2000] });
    // real proof is the user_denied tool result above; the NL wording just has to
    // reflect that the agent did NOT run it
    const reply = await assistantReply(page, cid, 90_000);
    expect(reply.toLowerCase()).toMatch(/từ chối|denied|deny|reject|restrict|can'?t|cannot|unable|not able|không|khong/);
    await shot(page, "C3-denied");
  });

  test("C4 workspace panel shows a file the agent wrote", async ({ page }) => {
    test.setTimeout(150_000);
    await login(page);
    const cid = await newChatWith(page, "openai");
    await send(page, "Chay Python tao file scn.txt voi noi dung 'e2e'. Dung execute_code.");
    const card = page.locator(".approval-card");
    await expect(card).toBeVisible({ timeout: 40_000 });
    await card.getByRole("button", { name: /^Approve$/ }).click();
    await assistantReply(page, cid, 90_000);
    await page.getByRole("button", { name: /Files/ }).click();
    await expect(page.locator(".workspace-panel")).toBeVisible({ timeout: 10_000 });
    await expect(page.locator(".workspace-panel .workspace-name", { hasText: "scn.txt" }))
      .toBeVisible({ timeout: 15_000 });
    await shot(page, "C4-workspace");
  });

  // ── D. Settings CRUD ────────────────────────────────────────────
  test("D1 Settings screen: all tabs present", async ({ page }) => {
    await login(page);
    await page.locator("a.foot-link[href='/settings']").click();
    await expect(page.locator(".settings-tabs")).toBeVisible();
    for (const t of ["Agents", "Knowledge", "Playground", "RAG eval", "Agent eval", "Cron jobs", "MCP servers", "Usage"])
      await expect(page.locator(".settings-tabs button", { hasText: t })).toBeVisible();
    await shot(page, "D1-settings");
  });

  test("D2 Agents tab: create + delete an agent", async ({ page }) => {
    await login(page);
    await page.goto("/settings");
    await page.locator(".settings-tabs button", { hasText: "Agents" }).click();
    await page.getByRole("button", { name: "+ New agent" }).click();
    await page.locator(".settings-form label", { hasText: "Name" }).locator("input").fill("scn-tmp-agent");
    await page.locator(".settings-form label", { hasText: "Model" }).locator("input").fill("gpt-4o-mini");
    await page.getByRole("button", { name: "Save" }).click();
    const row = page.locator(".settings-table tr", { hasText: "scn-tmp-agent" });
    await expect(row).toBeVisible({ timeout: 10_000 });
    // destructive actions go through <ConfirmDialogHost/> now, not window.confirm()
    await row.getByRole("button", { name: "Delete" }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Delete" }).click();
    await expect(row).toHaveCount(0, { timeout: 10_000 });
    await shot(page, "D2-agents");
  });

  test("D3 MCP tab: unreachable http server → error", async ({ page }) => {
    await login(page);
    await page.goto("/settings");
    await page.locator(".settings-tabs button", { hasText: "MCP servers" }).click();
    await page.getByRole("button", { name: "+ New server" }).click();
    await page.locator(".settings-form label", { hasText: "Name" }).locator("input").fill("scn-bad-mcp");
    await page.locator("select").filter({ hasText: "http" }).selectOption("http");
    await page.locator(".settings-form label", { hasText: "Config" }).locator("textarea")
      .fill('{"url":"http://127.0.0.1:9"}');
    await page.getByRole("button", { name: "Save" }).click();
    await expect(page.locator(".error-banner")).toBeVisible({ timeout: 15_000 });
    await shot(page, "D3-mcp-error");
  });

  test("D4 Cron tab: create job + Run now", async ({ page }) => {
    test.setTimeout(120_000);
    await login(page);
    await page.goto("/settings");
    await page.locator(".settings-tabs button", { hasText: "Cron jobs" }).click();
    await page.getByRole("button", { name: "+ New job" }).click();
    await page.locator(".settings-form label", { hasText: "Name" }).locator("input").fill("scn-cron");
    await page.locator(".settings-form label", { hasText: "Prompt" }).locator("textarea").fill("Noi dung: cron-ok");
    await page.getByRole("button", { name: "Save" }).click();
    const row = page.locator(".settings-table tr", { hasText: "scn-cron" });
    await expect(row).toBeVisible({ timeout: 10_000 });
    await row.getByRole("button", { name: "Run now" }).click();
    // cron run-now creates a fresh conversation -> visible on the sidebar
    await page.goto("/");
    await expect(page.locator(".conv-item", { hasText: /cron/i }).first()).toBeVisible({ timeout: 25_000 });
    await shot(page, "D4-cron");
    // cleanup
    await page.goto("/settings");
    await page.locator(".settings-tabs button", { hasText: "Cron jobs" }).click();
    await page.locator(".settings-table tr", { hasText: "scn-cron" }).getByRole("button", { name: "Delete" }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Delete" }).click();
    await expect(page.locator(".settings-table tr", { hasText: "scn-cron" })).toHaveCount(0, { timeout: 10_000 });
  });

  // ── E. RAG / Knowledge Base ────────────────────────────────────
  const DOCS: [string, string][] = [
    ["Nghi phep", "Nhan vien chinh thuc duoc 20 ngay nghi phep co luong moi nam. Nghi phep chua dung duoc chuyen toi da 5 ngay sang nam sau. Nghi om tach rieng, toi da 10 ngay moi nam."],
    ["Hoan tien", "Chinh sach hoan tien cho phep tra hang trong vong 30 ngay ke tu ngay mua. Su co thanh toan gan nhat duoc theo doi o ticket JIRA-4471. Lien he billing@acme.io."],
    ["Onboarding", "Ngay dau tien: nhan laptop va the ra vao, tham gia buoi dinh huong luc 10 gio. Tai khoan email do IT cap trong vong 24 gio."],
    ["Chi phi", "Bao cao chi phi phai nop truoc ngay mung 5 hang thang. Han muc chi khong can phe duyet la 2 trieu dong."],
  ];

  test("E1 create collection + ingest 4 docs → all ready", async ({ page }) => {
    test.setTimeout(120_000);
    await login(page);
    // clean any prior run
    const list = await api(page, "GET", "/kb/collections");
    for (const c of list.json || []) if (c.name === "acme-hr") await api(page, "DELETE", `/kb/collections/${c.id}`);
    const created = await api(page, "POST", "/kb/collections", { name: "acme-hr" });
    expect(created.status).toBe(201);
    const cid = created.json.id;
    for (const [title, text] of DOCS) {
      const r = await api(page, "POST", `/kb/collections/${cid}/documents/from-text`, { title, text });
      expect(r.status).toBe(202);
    }
    await expect(async () => {
      const docs = await api(page, "GET", `/kb/collections/${cid}/documents`);
      const st = (docs.json || []).map((d: any) => d.status);
      expect(st).toEqual(["ready", "ready", "ready", "ready"]);
    }).toPass({ timeout: 90_000, intervals: [2000] });
    const coll = await api(page, "GET", `/kb/collections/${cid}`);
    expect(coll.json.chunk_count).toBeGreaterThan(0);
    expect(coll.json.doc_count).toBe(4);
  });

  test("E2 hybrid search + 9-stage pipeline + BM25 exact-ID", async ({ page }) => {
    await login(page);
    const cid = (await api(page, "GET", "/kb/collections")).json.find((c: any) => c.name === "acme-hr").id;

    const s1 = await api(page, "POST", "/kb/search", {
      query: "nhan vien duoc bao nhieu ngay nghi phep", collection_ids: [cid], explain: true,
    });
    expect(s1.status).toBe(200);
    expect(s1.json.chunks.length).toBeGreaterThan(0);
    expect(s1.json.chunks[0].text.toLowerCase()).toContain("20 ngay");
    const stageNames = s1.json.stages.map((x: any) => x.name);
    expect(stageNames).toEqual(["transform", "dense", "sparse", "fuse", "dedupe", "rerank", "expand", "grade", "pack"]);
    expect(s1.json.context).toContain("<retrieved_context>");

    const s2 = await api(page, "POST", "/kb/search", {
      query: "JIRA-4471", collection_ids: [cid], explain: true, config: { rerank_mode: "none" },
    });
    expect(s2.json.chunks[0].text).toContain("JIRA-4471");
    const sparse = s2.json.stages.find((x: any) => x.name === "sparse");
    expect(sparse.skipped).toBeFalsy();
    expect(sparse.candidate_count).toBeGreaterThan(0);

    const s3 = await api(page, "POST", "/kb/search", {
      query: "hoan toan khong lien quan xyzzy", collection_ids: [cid], config: { score_threshold: 0.99 },
    });
    expect(s3.json.no_context).toBe(true);
  });

  test("E3 agent mode=always → citation chip in chat + rag persisted on conversation payload", async ({ page }) => {
    test.setTimeout(120_000);
    await login(page);
    const cid = (await api(page, "GET", "/kb/collections")).json.find((c: any) => c.name === "acme-hr").id;
    const agents = (await api(page, "GET", "/agents")).json;
    const oa = agents.find((a: any) => a.provider === "openai");
    await api(page, "PATCH", `/agents/${oa.id}`, { kb_collection_ids: [cid], rag_config: { mode: "always" } });
    try {
      await page.goto("/");
      const cid2 = await newChatWith(page, "openai");
      await send(page, "Nhan vien duoc bao nhieu ngay nghi phep mot nam?");
      const reply = await assistantReply(page, cid2, 90_000);
      expect(reply).toMatch(/20/);
      // citation chip rendered in the chat
      await expect(page.locator(".citations .citation-chip").first()).toBeVisible({ timeout: 10_000 });
      await shot(page, "E3a-citation");
      // and it's persisted on the conversation payload (survives any reload / new client)
      const detail = (await api(page, "GET", `/conversations/${cid2}`)).json;
      const withRag = detail.messages.filter((m: any) => m.role === "assistant" && m.rag);
      expect(withRag.length).toBeGreaterThan(0);
      expect(withRag[0].rag.citations.length).toBeGreaterThan(0);
    } finally {
      await api(page, "PATCH", `/agents/${oa.id}`, { kb_collection_ids: [], rag_config: {} });
    }
  });

  test("E4 Playground tab: run query → stage list renders", async ({ page }) => {
    await login(page);
    await page.goto("/settings");
    await page.locator(".settings-tabs button", { hasText: "Playground" }).click();
    await page.locator("fieldset", { hasText: "Collections" }).locator("label", { hasText: "acme-hr" }).locator("input").check();
    await page.locator("label", { hasText: "Query" }).locator("input").fill("han nop bao cao chi phi");
    await page.getByRole("button", { name: /^Run$/ }).click();
    await expect(page.locator(".stage-list .stage")).toHaveCount(9, { timeout: 30_000 });
    await expect(page.locator(".playground-result")).toContainText(/pack/);
    await shot(page, "E4-playground");
  });

  test("E5 Evaluation: generate golden set + run + metrics", async ({ page }) => {
    test.setTimeout(240_000);
    await login(page);
    const cid = (await api(page, "GET", "/kb/collections")).json.find((c: any) => c.name === "acme-hr").id;
    const gen = await api(page, "POST", `/kb/collections/${cid}/eval/generate`, { n: 4 });
    expect(gen.status).toBe(202);
    expect(gen.json.cases.length).toBeGreaterThan(0);
    const run = await api(page, "POST", "/kb/eval-runs", {
      collection_id: cid, name: "scn-eval", cases: gen.json.cases, config: { rerank_mode: "none" },
    });
    expect(run.status).toBe(202);
    expect(run.json.status).toBe("running");
    let final: any;
    await expect(async () => {
      final = (await api(page, "GET", `/kb/eval-runs/${run.json.id}`)).json;
      expect(final.run.status).toBe("done");
    }).toPass({ timeout: 210_000, intervals: [5000] });
    // at least one of the 4 metrics computed
    const m = final.run;
    expect([m.faithfulness, m.answer_relevancy, m.context_precision, m.context_recall].some((x) => x !== null)).toBe(true);
    expect(final.cases.length).toBe(gen.json.cases.length);
    // show it in the UI too
    await page.goto("/settings");
    await page.locator(".settings-tabs button", { hasText: "Eval" }).click();
    await expect(page.locator(".settings-table tr", { hasText: "scn-eval" })).toBeVisible({ timeout: 10_000 });
    await shot(page, "E5-eval");
    await api(page, "DELETE", `/kb/eval-runs/${run.json.id}`);
  });

  test("E6 cleanup: drop the scenario collection", async ({ page }) => {
    await login(page);
    const list = await api(page, "GET", "/kb/collections");
    for (const c of list.json || []) if (c.name === "acme-hr") {
      const r = await api(page, "DELETE", `/kb/collections/${c.id}`);
      expect(r.status).toBe(204);
    }
  });

  // ── F. Usage / observability ───────────────────────────────────
  test("F1 Usage tab: cost split by kind shows embedding", async ({ page }) => {
    await login(page);
    await page.goto("/settings");
    await page.locator(".settings-tabs button", { hasText: "Usage" }).click();
    await page.locator("select").last().selectOption("kind");
    await expect(page.locator(".settings-table")).toContainText(/embedding|chat/, { timeout: 15_000 });
    await shot(page, "F1-usage");
  });

  test("F2 logout", async ({ page }) => {
    await login(page);
    await page.getByRole("button", { name: "Logout" }).click();
    await expect(page).toHaveURL(/\/login$/);
  });
});
