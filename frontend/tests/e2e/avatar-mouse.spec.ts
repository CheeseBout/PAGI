/**
 * Avatar mouse interaction (SPEC §20.14, Phase 21) against the real dev
 * servers + a real Cubism model (fuwaka, via the "openai" agent — already
 * configured with avatar_config.enabled=true in this environment's DB).
 *
 * fuwaka declares no `HitAreas`/`Expressions`/`Motions` in its model3.json,
 * so hover/tap reactions can only be verified as *silent no-ops* here
 * (§20.14.6) — not as visible expression/motion changes. Look-follow is
 * verified precisely via the `__lastDragNdc` test hook AvatarCanvas.tsx sets
 * on every mousemove (mirrors the pre-existing `__frames` hook in the same
 * file), rather than by diffing screenshots, which would be noisy: the
 * model's own idle blink/breathing keeps rendering slightly different frames
 * even with the cursor perfectly still.
 *
 * Run: `npm run test:e2e -- avatar-mouse.spec.ts` (backend :8000 + this dev
 * server must be up; sandbox not required for these tests).
 */
import { test, expect, Page, Locator } from "@playwright/test";
import * as fs from "fs";

const SHOTS = "e2e-screenshots";
fs.mkdirSync(SHOTS, { recursive: true });
const shot = (p: Page, n: string) => p.screenshot({ path: `${SHOTS}/avm-${n}.png` }).catch(() => {});

async function login(page: Page) {
  // Always fill+submit rather than branching on page.url() first (as
  // scenario.spec.ts's helper does): right after goto("/"), the SPA hasn't
  // finished its own client-side redirect to /login yet, so url() can still
  // read "/" for a moment and an early-return-if-already-logged-in check
  // skips login entirely — this bit exactly during this feature's own e2e
  // authoring. Waiting on the login form's visibility instead sidesteps the
  // race regardless of which state we land on.
  await page.goto("/");
  await expect(page.locator("form.login-card")).toBeVisible();
  await page.locator('input[type="password"]').fill("admin");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.locator(".app-shell")).toBeVisible({ timeout: 15_000 });
}

async function newChatWith(page: Page, providerText: string) {
  const picker = page.locator("select.model-picker");
  const opt = picker.locator("option", { hasText: providerText });
  await picker.selectOption((await opt.getAttribute("value")) as string);
  await page.getByRole("button", { name: "+ New chat" }).click();
  await expect(page.locator(".ws-dot.on")).toBeVisible({ timeout: 15_000 });
}

async function avatarReady(page: Page): Promise<Locator> {
  await expect(page.locator('.avatar-column [data-avatar-status="ready"]')).toBeVisible({ timeout: 20_000 });
  return page.locator(".avatar-column canvas");
}

async function lastDragNdc(canvas: Locator): Promise<{ x: number; y: number } | null> {
  return canvas.evaluate((el) => (el as HTMLCanvasElement & { __lastDragNdc?: { x: number; y: number } }).__lastDragNdc ?? null);
}

test.describe("Avatar mouse interaction (SPEC §20.14)", () => {
  // Each test opens a fresh "New chat"; delete it through the UI afterwards
  // (same path as ui.spec.ts test 7) so runs don't pile empty conversations
  // into the real conversation list.
  test.afterEach(async ({ page }) => {
    const del = page.locator(".conv-item.active .conv-del");
    if (await del.count()) await del.click().catch(() => {});
  });

  test("1. look-follow: NDC target tracks the cursor to all four edges of the canvas", async ({ page }) => {
    await login(page);
    await newChatWith(page, "openai");
    const canvas = await avatarReady(page);
    const box = await canvas.boundingBox();
    if (!box) throw new Error("avatar canvas has no bounding box");
    const margin = 4;

    await page.mouse.move(box.x + margin, box.y + box.height / 2);
    await expect(async () => {
      const ndc = await lastDragNdc(canvas);
      expect(ndc?.x).toBeLessThan(-0.8);
    }).toPass({ timeout: 3_000 });
    await shot(page, "01-left");

    await page.mouse.move(box.x + box.width - margin, box.y + box.height / 2);
    await expect(async () => {
      const ndc = await lastDragNdc(canvas);
      expect(ndc?.x).toBeGreaterThan(0.8);
    }).toPass({ timeout: 3_000 });
    await shot(page, "02-right");

    // Y is flipped to Cubism's up-positive convention (§20.14.2): top of the
    // canvas must read as +1, not -1.
    await page.mouse.move(box.x + box.width / 2, box.y + margin);
    await expect(async () => {
      const ndc = await lastDragNdc(canvas);
      expect(ndc?.y).toBeGreaterThan(0.8);
    }).toPass({ timeout: 3_000 });
    await shot(page, "03-top");

    await page.mouse.move(box.x + box.width / 2, box.y + box.height - margin);
    await expect(async () => {
      const ndc = await lastDragNdc(canvas);
      expect(ndc?.y).toBeLessThan(-0.8);
    }).toPass({ timeout: 3_000 });
    await shot(page, "04-bottom");
  });

  test("2. cursor leaving the document resets look-follow to center (§20.14.2)", async ({ page }) => {
    await login(page);
    await newChatWith(page, "openai");
    const canvas = await avatarReady(page);
    const box = await canvas.boundingBox();
    if (!box) throw new Error("avatar canvas has no bounding box");

    await page.mouse.move(box.x + 4, box.y + box.height / 2);
    await expect(async () => {
      const ndc = await lastDragNdc(canvas);
      expect(ndc?.x).toBeLessThan(-0.5);
    }).toPass({ timeout: 3_000 });

    // Dispatched directly rather than relying on Playwright's mouse leaving
    // the viewport, which headless Chromium doesn't reliably turn into a real
    // `document` mouseleave — this exercises the exact listener AvatarCanvas
    // registers either way.
    await page.evaluate(() => document.dispatchEvent(new MouseEvent("mouseleave")));
    await expect(async () => {
      const ndc = await lastDragNdc(canvas);
      expect(ndc).toEqual({ x: 0, y: 0 });
    }).toPass({ timeout: 3_000 });
    await shot(page, "05-reset-after-leave");
  });

  test("3. hover/click storm + a real drag across a HitArea-less model: zero console errors, avatar stays ready (§20.14.6)", async ({
    page,
  }) => {
    await login(page);
    await newChatWith(page, "openai");
    const canvas = await avatarReady(page);
    const box = await canvas.boundingBox();
    if (!box) throw new Error("avatar canvas has no bounding box");
    const cx = box.x + box.width / 2;
    const cy = box.y + box.height / 2;

    // Listeners start only now, not from page load: App.tsx's one-shot
    // `api.me()` auth check on mount legitimately 401s before login (and
    // fires twice under React StrictMode) — that's routine bootstrap noise,
    // unrelated to the avatar, and would otherwise make this assertion
    // meaningless.
    const errors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") errors.push(msg.text());
    });
    page.on("pageerror", (err) => errors.push(String(err)));
    page.on("response", (res) => {
      if (res.status() >= 400) errors.push(`HTTP ${res.status()} ${res.request().method()} ${res.url()}`);
    });

    // hover sweep across the model
    for (let i = 0; i < 10; i++) {
      await page.mouse.move(cx + (i - 5) * 5, cy + (i - 5) * 3);
    }
    // rapid taps, well under TAP_COOLDOWN_MS between them
    for (let i = 0; i < 8; i++) {
      await page.mouse.click(cx, cy);
    }
    // a real drag across the canvas (mousedown -> move -> up) must not react
    // either — it's a drag by §20.14.5's own definition, not a tap
    await page.mouse.move(cx - 20, cy);
    await page.mouse.down();
    await page.mouse.move(cx + 40, cy, { steps: 10 });
    await page.mouse.up();

    await page.waitForTimeout(500); // let any async console output land
    await expect(page.locator('.avatar-column [data-avatar-status="ready"]')).toBeVisible();
    await shot(page, "06-after-interaction-storm");
    expect(errors).toEqual([]);
  });

  test("4. cursor style never gets stuck as 'pointer' once the pointer leaves the canvas", async ({ page }) => {
    await login(page);
    await newChatWith(page, "openai");
    const canvas = await avatarReady(page);
    const box = await canvas.boundingBox();
    if (!box) throw new Error("avatar canvas has no bounding box");

    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.move(box.x + box.width + 50, box.y + box.height / 2); // off the canvas, still in the window
    await expect(async () => {
      const cursor = await canvas.evaluate((el) => (el as HTMLCanvasElement).style.cursor);
      expect(cursor).toBe("");
    }).toPass({ timeout: 3_000 });
  });

  test("5. look-follow is anchored at the model's eyes, not the canvas centre (§20.14.2)", async ({ page }) => {
    await login(page);
    await newChatWith(page, "openai");
    const canvas = await avatarReady(page);
    const box = await canvas.boundingBox();
    if (!box) throw new Error("avatar canvas has no bounding box");
    const cx = box.x + box.width / 2;

    await page.mouse.move(cx, box.y + box.height / 2);
    await expect(async () => {
      const anchor = await canvas.evaluate((el) => (el as HTMLCanvasElement & { __eyeAnchorNdcY?: number }).__eyeAnchorNdcY);
      // full-body model: eyes sit well above the canvas centre (measured ~0.69 on fuwaka)
      expect(anchor).toBeGreaterThan(0.4);
      expect(anchor).toBeLessThan(0.95);
    }).toPass({ timeout: 5_000 });

    // a cursor level with the canvas centre is *below* the eyes -> looks down
    expect((await lastDragNdc(canvas))?.y).toBeLessThan(-0.2);

    // a cursor level with the eyes reads as straight ahead
    const anchor = (await canvas.evaluate((el) => (el as HTMLCanvasElement & { __eyeAnchorNdcY?: number }).__eyeAnchorNdcY)) as number;
    await page.mouse.move(cx, box.y + ((1 - anchor) / 2) * box.height);
    await expect(async () => {
      expect(Math.abs((await lastDragNdc(canvas))?.y ?? 9)).toBeLessThan(0.05);
    }).toPass({ timeout: 3_000 });
  });
});
