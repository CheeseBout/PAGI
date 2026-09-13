import { test, expect, Page } from "@playwright/test";
import * as fs from "fs";

// cwd is the frontend/ dir (where playwright.config.ts lives)
const SHOTS = "e2e-screenshots";
fs.mkdirSync(SHOTS, { recursive: true });
const shot = (p: Page, name: string) => p.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });

const USER = "admin";
const PASS = "admin";

async function login(page: Page) {
  await page.goto("/");
  await expect(page.locator("form.login-card")).toBeVisible();
  await page.locator('input[type="password"]').fill(PASS);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.locator(".app-shell")).toBeVisible({ timeout: 15_000 });
}

test.describe("PAGI Web UI", () => {
  test("1. login page renders (Phase 7.2)", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveURL(/\/login$/);
    await expect(page.getByRole("heading", { name: "PAGI" })).toBeVisible();
    await expect(page.locator("form.login-card")).toBeVisible();
    await expect(page.locator('input').first()).toHaveValue("admin"); // username prefilled
    await shot(page, "01-login");
  });

  test("2. wrong password shows error banner", async ({ page }) => {
    await page.goto("/");
    await page.locator('input[type="password"]').fill("definitely-wrong");
    await page.getByRole("button", { name: "Sign in" }).click();
    const banner = page.locator(".error-banner");
    await expect(banner).toBeVisible();
    await expect(banner).toContainText(/wrong/i);
    await expect(page).toHaveURL(/\/login$/);
    await shot(page, "02-login-error");
  });

  test("3. successful login -> chat shell + sidebar (Phase 7.1)", async ({ page }) => {
    await login(page);
    await expect(page.locator(".sidebar")).toBeVisible();
    await expect(page.getByRole("button", { name: "+ New chat" })).toBeVisible();
    await expect(page.locator(".sidebar-foot")).toContainText("admin");
    await expect(page.getByRole("button", { name: "Logout" })).toBeVisible();
    await shot(page, "03-chat-shell");
  });

  test("4. model picker lists the 4 seeded agents (Phase 7.6)", async ({ page }) => {
    await login(page);
    const picker = page.locator("select.model-picker");
    await expect(picker).toBeVisible();
    const opts = picker.locator("option");
    await expect(opts).toHaveCount(4);
    const texts = (await opts.allTextContents()).join(" | ");
    for (const p of ["openai", "anthropic", "gemini", "openrouter"]) expect(texts).toContain(p);
    await shot(page, "04-model-picker");
  });

  test("5. new chat -> enters conversation, input enabled, ws connects (Phase 7.1/7.5)", async ({ page }) => {
    await login(page);
    await page.getByRole("button", { name: "+ New chat" }).click();
    // a fresh conversation becomes the active one
    await expect(page.locator(".conv-item.active")).toHaveCount(1);
    await expect(page.locator(".conv-item.active .conv-title")).toHaveText("New chat");
    await expect(page.locator(".input-box textarea")).toBeEnabled();
    await expect(page.locator(".ws-dot.on")).toBeVisible({ timeout: 10_000 });
    await shot(page, "05-new-chat");
  });

  test("6. send message on Anthropic agent -> provider_error rendered in UI (Phase 7.3 error path)", async ({ page }) => {
    await login(page);
    // pick the Anthropic agent (no API key -> deterministic fast provider_error, no LLM hang)
    const picker = page.locator("select.model-picker");
    const anthOpt = picker.locator("option", { hasText: "anthropic" });
    await picker.selectOption(await anthOpt.getAttribute("value") as string);
    await page.getByRole("button", { name: "+ New chat" }).click();
    await expect(page.locator(".ws-dot.on")).toBeVisible({ timeout: 10_000 });
    await page.locator(".input-box textarea").fill("hello there");
    await page.getByRole("button", { name: "Send" }).click();
    // user bubble shows immediately (optimistic render)
    await expect(page.getByText("hello there").first()).toBeVisible();
    // then an inline error banner from the WS "error" event (provider_error)
    await expect(page.locator(".error-banner.inline")).toBeVisible({ timeout: 20_000 });
    await shot(page, "06-provider-error");
  });

  test("7. delete the active conversation drops it and returns to empty state (Phase 7.5)", async ({ page }) => {
    await login(page);
    await page.getByRole("button", { name: "+ New chat" }).click();
    // now inside a conversation: the composer is shown
    await expect(page.locator(".input-box textarea")).toBeVisible();
    const firstItem = page.locator(".conv-item").first();
    await expect(firstItem).toHaveClass(/active/);
    await firstItem.locator(".conv-del").click();
    // that conversation was currentId -> deleting it clears selection -> empty state button
    await expect(page.getByRole("button", { name: "+ Start a new chat" })).toBeVisible();
    await shot(page, "07-after-delete");
  });

  test("8. dark/light theme toggle flips data-theme (Phase 7.8)", async ({ page }) => {
    await login(page);
    const root = page.locator("html");
    const before = await root.getAttribute("data-theme");
    await page.locator(".sidebar-foot button[title='Toggle theme']").click();
    await expect(async () => {
      expect(await root.getAttribute("data-theme")).not.toBe(before);
    }).toPass({ timeout: 5_000 });
    const after = await root.getAttribute("data-theme");
    expect([before, after].sort()).toEqual(["dark", "light"]);
    await shot(page, `08-theme-${after}`);
  });

  test("9. session persists across reload (cookie auth)", async ({ page }) => {
    await login(page);
    await page.reload();
    await expect(page.locator(".app-shell")).toBeVisible({ timeout: 15_000 });
    await expect(page).not.toHaveURL(/\/login$/);
    await shot(page, "09-reload-persisted");
  });

  test("10. logout returns to login page", async ({ page }) => {
    await login(page);
    await page.getByRole("button", { name: "Logout" }).click();
    await expect(page).toHaveURL(/\/login$/);
    await expect(page.locator("form.login-card")).toBeVisible();
    await shot(page, "10-after-logout");
  });
});
