// The `/screen` command typed in the overlay's input (SPEC §21.9): one of the only
// three ways a capture can start (button, hotkey, this). Pure so it can be tested.

export interface ScreenCommand {
  isCommand: boolean;
  /** text after the command, e.g. the question in `/screen giải thích lỗi này` */
  rest: string;
}

const RE = /^\/screen(?:\s+([\s\S]*))?$/i;

export function parseScreenCommand(text: string): ScreenCommand {
  const m = RE.exec(text.trim());
  if (!m) return { isCommand: false, rest: text };
  return { isCommand: true, rest: (m[1] ?? "").trim() };
}
