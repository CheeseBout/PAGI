# Overlay e2e checks

Playwright scripts that drive the **real** overlay: a browser tab for the page, and
the real Electron shell for everything a browser can't show (transparent window,
click-through, drag, tray logout, region capture, toasts).

```bash
# 1. backend on :8000 and the frontend dev server on :5173 (see the root README)
# 2. build the shell once (and after changing desktop/src)
cd desktop && npm run build
# 3. run everything (~6–8 min), or just some scripts by name
npm run e2e
npm run e2e -- capture        # only scripts whose file name contains "capture"
```

| Script | What it checks |
|---|---|
| `ui.cjs` | Bubble rules (short/long/code/tool), fade-out and hover, approval card, error, no page scroll — chat and notification sockets are **mocked**, REST is real |
| `capture-ui.cjs` | Screenshot flow in the page: no automatic capture, chip, `/screen`, send/upload, replace, hotkey relay, upload failure, vision off — capture is **stubbed**, upload **intercepted** |
| `shell.cjs` | The Electron window: transparent (real pixels), always-on-top, sizes, click-through, drag, renderer isolation, navigation lock, `openExternal`, toasts |
| `capture-shell.cjs` | Real region capture on Electron: selector windows, cancel paths, crop size, hotkey while hidden. **Captures your real screen into memory only; upload and chat are intercepted, so nothing leaves the machine** |
| `perf-logout-shell.cjs` | Idle frame-rate drop (real GPU) and the tray "Đăng xuất" action |

Notes

- They use your **real backend and database** (a login, plus short-lived overlay
  sessions / a cron job that they delete afterwards). Point `PAGI_E2E_URL`,
  `PAGI_E2E_USER`, `PAGI_E2E_PASSWORD` at another instance if you'd rather not.
- Each Electron script uses a throwaway `--user-data-dir`, so it never touches your
  own overlay's cookies, position or single-instance lock.
- Electron scripts open real windows on your desktop for a while; a cursor moving
  over them is real input (the idle check accounts for that).
- Test-only switches in the shell: `PAGI_OVERLAY_TOAST_LOG` (toasts are logged, not
  shown) and `PAGI_OVERLAY_TEST_HOOKS=1` (exposes the tray actions).
- Screenshots the UI checks write go to `%TEMP%/pagi-e2e-shots`.
