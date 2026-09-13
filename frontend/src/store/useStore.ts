import { create } from "zustand";

type Theme = "light" | "dark";

function initialTheme(): Theme {
  const saved = localStorage.getItem("pagi-theme");
  if (saved === "light" || saved === "dark") return saved;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

interface AppState {
  user: { id: string; username: string } | null;
  theme: Theme;
  setUser: (u: AppState["user"]) => void;
  toggleTheme: () => void;
}

export const useStore = create<AppState>((set) => ({
  user: null,
  theme: initialTheme(),
  setUser: (user) => set({ user }),
  toggleTheme: () =>
    set((s) => {
      const theme: Theme = s.theme === "dark" ? "light" : "dark";
      localStorage.setItem("pagi-theme", theme);
      document.documentElement.dataset.theme = theme;
      return { theme };
    }),
}));

// apply persisted theme on load
document.documentElement.dataset.theme = useStore.getState().theme;
