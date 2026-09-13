import { FormEvent, useState } from "react";
import { api, ApiError } from "../api/client";
import { useStore } from "../store/useStore";
import { Button } from "@/components/ui/button";
import { ErrorBanner } from "@/components/ui/error-banner";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export default function Login() {
  const setUser = useStore((s) => s.setUser);
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const { user } = await api.login(username, password);
      setUser(user);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="center-screen flex min-h-dvh flex-col items-center justify-center gap-4 p-4">
      <form
        className="card login-card w-80 rounded-md border border-border bg-bg-elev p-7 flex flex-col gap-3"
        onSubmit={onSubmit}
      >
        <h1 className="m-0 text-xl font-semibold">PAGI</h1>
        <p className="text-muted">Sign in to your agent gateway</p>
        <Label className="flex flex-col gap-1">
          Username
          <Input value={username} onChange={(e) => setUsername(e.target.value)} autoFocus />
        </Label>
        <Label className="flex flex-col gap-1">
          Password
          <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </Label>
        <ErrorBanner message={error} />
        <Button type="submit" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </Button>
      </form>
    </div>
  );
}
