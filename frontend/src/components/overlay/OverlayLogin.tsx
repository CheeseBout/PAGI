// Inline sign-in for the overlay (SPEC §21.6). The Electron window keeps its
// own cookie jar, so the first launch — and any expired session — lands here.
import { FormEvent, useState } from "react";
import { api, ApiError } from "@/api/client";
import { HIT_ATTR } from "@/features/overlay/hitTest";

const hit = { [HIT_ATTR]: "" };

export default function OverlayLogin({ onDone }: { onDone: () => void }) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api.login(username, password);
      onDone();
    } catch (err) {
      // 429 carries "Try again in Ns" verbatim from the server (SPEC §21.6)
      setError(err instanceof ApiError ? err.message : "Không đăng nhập được");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      {...hit}
      onSubmit={submit}
      className="flex flex-col gap-2 rounded-2xl border border-border bg-bg-elev p-3 text-sm shadow-lg"
    >
      <div className="text-xs text-muted">Đăng nhập PAGI (chỉ cần một lần)</div>
      <input
        className="rounded border border-border bg-bg p-1.5"
        value={username}
        autoComplete="username"
        onChange={(e) => setUsername(e.target.value)}
      />
      <input
        className="rounded border border-border bg-bg p-1.5"
        type="password"
        placeholder="Mật khẩu"
        autoComplete="current-password"
        autoFocus
        value={password}
        onChange={(e) => setPassword(e.target.value)}
      />
      {error && <span className="text-xs text-danger">{error}</span>}
      <button className="rounded-md bg-accent p-1.5 text-accent-fg disabled:opacity-50" type="submit" disabled={busy}>
        {busy ? "Đang đăng nhập…" : "Đăng nhập"}
      </button>
    </form>
  );
}
