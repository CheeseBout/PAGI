import { describe, expect, it } from "vitest";
import {
  clampToArea,
  cropRectFromSelection,
  fitLongestEdge,
  defaultRect,
  isAllowedExternalUrl,
  isAllowedNavigation,
  isInsecureRemote,
  isOverlayMode,
  isTrustedSender,
  parseNotification,
  restoreRect,
  sizeForMode,
} from "./policy";

const ORIGIN = "http://localhost:5173";
const area = { x: 0, y: 0, width: 1920, height: 1040 };

describe("IPC / navigation policy (SPEC §21.11, §21.13)", () => {
  it("trusts only frames of the overlay origin", () => {
    expect(isTrustedSender("http://localhost:5173/overlay", ORIGIN)).toBe(true);
    expect(isTrustedSender("http://evil.example/overlay", ORIGIN)).toBe(false);
    expect(isTrustedSender("http://localhost:5174/overlay", ORIGIN)).toBe(false);
    expect(isTrustedSender("file:///C:/x.html", ORIGIN)).toBe(false);
    expect(isTrustedSender(undefined, ORIGIN)).toBe(false);
    expect(isTrustedSender("not a url", ORIGIN)).toBe(false);
  });

  it("locks in-window navigation to the overlay origin", () => {
    expect(isAllowedNavigation("http://localhost:5173/login", ORIGIN)).toBe(true);
    expect(isAllowedNavigation("https://example.com", ORIGIN)).toBe(false);
  });

  it("openExternal accepts only http(s) URLs of the overlay origin", () => {
    expect(isAllowedExternalUrl("http://localhost:5173/chat/abc", ORIGIN)).toBe(true);
    expect(isAllowedExternalUrl("http://evil.example/", ORIGIN)).toBe(false);
    expect(isAllowedExternalUrl("file:///C:/Windows/System32/calc.exe", ORIGIN)).toBe(false);
    expect(isAllowedExternalUrl("javascript:alert(1)", ORIGIN)).toBe(false);
    expect(isAllowedExternalUrl("ftp://localhost:5173/x", ORIGIN)).toBe(false);
    expect(isAllowedExternalUrl("", ORIGIN)).toBe(false);
  });

  it("flags plain-HTTP non-loopback origins", () => {
    expect(isInsecureRemote("http://192.168.1.5:5173")).toBe(true);
    expect(isInsecureRemote("http://localhost:5173")).toBe(false);
    expect(isInsecureRemote("http://127.0.0.1:5173")).toBe(false);
    expect(isInsecureRemote("https://pagi.example.com")).toBe(false);
  });
});

describe("window geometry (SPEC §21.3)", () => {
  it("validates modes and maps them to sizes", () => {
    expect(isOverlayMode("panel")).toBe(true);
    expect(isOverlayMode("huge")).toBe(false);
    expect(isOverlayMode(undefined)).toBe(false);
    expect(sizeForMode("panel").width).toBeGreaterThan(sizeForMode("compact").width);
  });

  it("default placement is the bottom-right corner of the primary display", () => {
    const r = defaultRect({ width: 360, height: 520 }, area, 24);
    expect(r).toEqual({ x: 1920 - 360 - 24, y: 1040 - 520 - 24, width: 360, height: 520 });
  });

  it("restores a saved spot only if it is still fully on a display", () => {
    const size = { width: 360, height: 520 };
    expect(restoreRect({ x: 100, y: 100, ...size }, size, [area], area)).toEqual({ x: 100, y: 100, ...size });
    // the display it was on is gone -> fall back to the default corner
    expect(restoreRect({ x: 3000, y: 100, ...size }, size, [area], area)).toEqual(defaultRect(size, area));
    expect(restoreRect(null, size, [area], area)).toEqual(defaultRect(size, area));
  });

  it("clamps a growing window back inside the work area", () => {
    const r = clampToArea({ x: 1700, y: 900, width: 780, height: 520 }, area);
    expect(r.x + r.width).toBeLessThanOrEqual(area.x + area.width);
    expect(r.y + r.height).toBeLessThanOrEqual(area.y + area.height);
  });
});

describe("notification payload validation (SPEC §21.11)", () => {
  const ok = { title: "PAGI cần bạn duyệt", body: "execute_code", kind: "approval", target: { type: "web", path: "/" }, tag: "a1" };

  it("accepts a well-formed payload and keeps the target intact", () => {
    expect(parseNotification(ok)).toEqual({ ...ok, tag: "a1" });
    expect(parseNotification({ ...ok, tag: undefined })?.tag).toBeNull();
  });

  it("rejects wrong shapes, unknown kinds and empty titles", () => {
    expect(parseNotification(null)).toBeNull();
    expect(parseNotification("x")).toBeNull();
    expect(parseNotification({ ...ok, title: 5 })).toBeNull();
    expect(parseNotification({ ...ok, body: undefined })).toBeNull();
    expect(parseNotification({ ...ok, kind: "malware" })).toBeNull();
    expect(parseNotification({ ...ok, title: "   " })).toBeNull();
    expect(parseNotification({ ...ok, tag: 42 })).toBeNull();
  });

  it("bounds text and refuses an oversized or cyclic target", () => {
    const long = parseNotification({ ...ok, title: "t".repeat(500), body: "b".repeat(5000) });
    expect(long?.title.length).toBe(120);
    expect(long?.body.length).toBe(300);
    expect(parseNotification({ ...ok, target: { blob: "x".repeat(2000) } })).toBeNull();
    const cyc: Record<string, unknown> = {};
    cyc.self = cyc;
    expect(parseNotification({ ...ok, target: cyc })).toBeNull();
  });
});

describe("region capture geometry (SPEC §21.9)", () => {
  const display = { width: 1536, height: 864 }; // CSS px of a 125%-scaled 1920x1080 display
  const image = { width: 1920, height: 1080 };

  it("scales a CSS-pixel selection to the physical pixels of the captured image", () => {
    expect(cropRectFromSelection({ x: 100, y: 80, width: 400, height: 200 }, display, image)).toEqual({
      x: 125, y: 100, width: 500, height: 250,
    });
  });

  it("clamps a drag that runs past the screen edge instead of failing", () => {
    const r = cropRectFromSelection({ x: 1400, y: 800, width: 500, height: 500 }, display, image);
    expect(r).not.toBeNull();
    expect(r!.x + r!.width).toBeLessThanOrEqual(image.width);
    expect(r!.y + r!.height).toBeLessThanOrEqual(image.height);
  });

  it("refuses a region smaller than 8px (a mis-click), on either side", () => {
    expect(cropRectFromSelection({ x: 10, y: 10, width: 7, height: 200 }, display, image)).toBeNull();
    expect(cropRectFromSelection({ x: 10, y: 10, width: 200, height: 3 }, display, image)).toBeNull();
    expect(cropRectFromSelection({ x: 10, y: 10, width: 8, height: 8 }, display, image)).not.toBeNull();
  });

  it("refuses garbage from the selector page and regions fully off-display", () => {
    expect(cropRectFromSelection(null, display, image)).toBeNull();
    expect(cropRectFromSelection("x", display, image)).toBeNull();
    expect(cropRectFromSelection({ x: NaN, y: 0, width: 50, height: 50 }, display, image)).toBeNull();
    expect(cropRectFromSelection({ x: 0, y: 0, width: Infinity, height: 50 }, display, image)).toBeNull();
    expect(cropRectFromSelection({ x: 5000, y: 5000, width: 50, height: 50 }, display, image)).toBeNull();
    expect(cropRectFromSelection({ x: 0, y: 0, width: 50, height: 50 }, { width: 0, height: 0 }, image)).toBeNull();
  });

  it("bounds the longest edge for huge displays and never upscales", () => {
    expect(fitLongestEdge({ width: 7680, height: 4320 })).toEqual({ width: 3000, height: 1688 });
    expect(fitLongestEdge({ width: 1920, height: 1080 })).toEqual({ width: 1920, height: 1080 });
    expect(fitLongestEdge({ width: 100, height: 4000 }, 2000)).toEqual({ width: 50, height: 2000 });
  });
});
