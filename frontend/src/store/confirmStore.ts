import { create } from "zustand";

interface ConfirmRequest {
  title: string;
  description?: string;
  confirmLabel?: string;
  variant?: "default" | "destructive";
}

interface ConfirmState extends ConfirmRequest {
  open: boolean;
  resolve: ((ok: boolean) => void) | null;
}

export const useConfirmStore = create<ConfirmState>(() => ({
  open: false,
  title: "",
  resolve: null,
}));

/** Promise-based replacement for `window.confirm` — resolves to the user's choice
 * instead of blocking the thread, and renders as a themed, keyboard-accessible
 * dialog via <ConfirmDialogHost/> (mounted once in App.tsx). */
export function confirmAction(req: ConfirmRequest): Promise<boolean> {
  return new Promise((resolve) => {
    useConfirmStore.setState({ ...req, open: true, resolve });
  });
}

export function resolveConfirm(ok: boolean) {
  const { resolve } = useConfirmStore.getState();
  useConfirmStore.setState({ open: false, resolve: null });
  resolve?.(ok);
}
