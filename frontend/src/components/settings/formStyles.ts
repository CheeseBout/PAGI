// Shared Tailwind classes for the native <select>/<input type=checkbox> elements
// across Settings tabs. These stay native HTML controls (not Radix) because
// several Playwright specs drive them directly via `.selectOption()` / `.check()`,
// which requires a real control in the accessibility tree.
export const selectCls =
  "h-8 rounded-lg border border-border bg-bg px-2 text-sm text-text focus-visible:outline-none focus-visible:border-accent";
export const checkboxCls = "size-4 rounded border-border accent-accent";
