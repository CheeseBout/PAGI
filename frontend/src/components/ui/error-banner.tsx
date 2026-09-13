import { cn } from "@/lib/utils";

/**
 * Shared error banner. `role="alert"` is the whole point of this wrapper —
 * every call site used to write the div by hand and none of them had it, so
 * a screen reader user got no signal at all when an action failed.
 */
export function ErrorBanner({ message, inline }: { message: string | null | undefined; inline?: boolean }) {
  if (!message) return null;
  return (
    <div
      role="alert"
      className={cn(
        "error-banner rounded-lg bg-danger-subtle px-2.5 py-2 text-2xs text-danger-subtle-fg",
        inline && "inline mx-4 mt-2",
      )}
    >
      {message}
    </div>
  );
}
