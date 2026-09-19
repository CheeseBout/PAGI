// Bootstrap of the overlay's identity (SPEC §21.5, §21.6): am I logged in, which
// agent am I talking to, and which continuous session is that. The Electron
// window has its own cookie jar, so the first run always starts logged out.
import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type Agent } from "@/api/client";

export type AuthState = "checking" | "out" | "in";

const AGENT_KEY = "pagi.overlay.agent";

function rememberedAgent(): string | null {
  try {
    return localStorage.getItem(AGENT_KEY);
  } catch {
    return null; // storage unavailable — falls back to the default agent
  }
}

/** SPEC §21.16 (4): last-used agent if it still exists, else the default one. */
export function chooseAgent(agents: Agent[], remembered: string | null): Agent | undefined {
  return (
    agents.find((a) => a.id === remembered) ?? agents.find((a) => a.is_default) ?? agents[0]
  );
}

export function useOverlaySession() {
  const [auth, setAuth] = useState<AuthState>("checking");
  const [agents, setAgents] = useState<Agent[]>([]);
  const [agentId, setAgentId] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [note, setNote] = useState("");

  const goLoggedOut = useCallback(() => {
    setSessionId(null);
    setAuth("out");
  }, []);

  useEffect(() => {
    api
      .me()
      .then(() => setAuth("in"))
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) setAuth("out");
        else setNote("Không kết nối được backend"); // network down: keep trying below
      });
  }, []);

  // retry while the backend is unreachable (user starts it themselves)
  useEffect(() => {
    if (auth !== "checking" || !note) return;
    const t = setTimeout(() => {
      setNote("");
      api
        .me()
        .then(() => setAuth("in"))
        .catch((e) => {
          if (e instanceof ApiError && e.status === 401) setAuth("out");
          else setNote("Không kết nối được backend");
        });
    }, 3000);
    return () => clearTimeout(t);
  }, [auth, note]);

  useEffect(() => {
    if (auth !== "in") return;
    let live = true;
    api
      .listAgents()
      .then((list) => {
        if (!live) return;
        setAgents(list);
        const chosen = chooseAgent(list, rememberedAgent());
        setAgentId(chosen?.id ?? null);
        if (!chosen) setNote("Chưa có agent nào — hãy tạo một agent ở web UI.");
      })
      .catch((e) => {
        if (!live) return;
        if (e instanceof ApiError && e.status === 401) goLoggedOut();
        else setNote("Không kết nối được backend");
      });
    return () => {
      live = false;
    };
  }, [auth, goLoggedOut]);

  useEffect(() => {
    if (auth !== "in" || !agentId) return;
    let live = true;
    api
      .overlaySession(agentId)
      .then((r) => live && setSessionId(r.session.id))
      .catch((e) => {
        if (!live) return;
        if (e instanceof ApiError && e.status === 401) goLoggedOut();
        else setNote(e instanceof Error ? e.message : "Không mở được phiên chat");
      });
    return () => {
      live = false;
    };
  }, [auth, agentId, goLoggedOut]);

  const selectAgent = useCallback((id: string) => {
    try {
      localStorage.setItem(AGENT_KEY, id);
    } catch {
      /* storage unavailable — choice just isn't remembered */
    }
    setSessionId(null);
    setAgentId(id);
  }, []);

  const agent = agents.find((a) => a.id === agentId);
  return { auth, setAuth, goLoggedOut, agents, agent, sessionId, note, selectAgent };
}
