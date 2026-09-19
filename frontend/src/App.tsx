import { useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { api } from "./api/client";
import ConfirmDialogHost from "./components/ConfirmDialogHost";
import Chat from "./pages/Chat";
import Login from "./pages/Login";
import Overlay from "./pages/Overlay";
import Settings from "./pages/Settings";
import { useStore } from "./store/useStore";
import { TooltipProvider } from "@/components/ui/tooltip";

export default function App() {
  const { user, setUser } = useStore();
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    api
      .me()
      .then((u) => setUser(u))
      .catch(() => setUser(null))
      .finally(() => setChecked(true));
  }, [setUser]);

  if (!checked)
    return (
      <div className="center-screen flex min-h-dvh flex-col items-center justify-center gap-4 p-4">
        Loading…
      </div>
    );

  return (
    <TooltipProvider delayDuration={300}>
      <Routes>
        <Route path="/login" element={user ? <Navigate to="/" replace /> : <Login />} />
        <Route path="/" element={user ? <Chat /> : <Navigate to="/login" replace />} />
        <Route
          path="/settings"
          element={user ? <Navigate to="/settings/agents" replace /> : <Navigate to="/login" replace />}
        />
        <Route path="/settings/:tab" element={user ? <Settings /> : <Navigate to="/login" replace />} />
        {/* desktop overlay (SPEC §21.4): handles its own auth — the Electron window has its own cookie jar */}
        <Route path="/overlay" element={<Overlay />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      <ConfirmDialogHost />
    </TooltipProvider>
  );
}
