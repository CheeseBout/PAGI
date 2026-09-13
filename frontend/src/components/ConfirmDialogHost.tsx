import { resolveConfirm, useConfirmStore } from "@/store/confirmStore";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

/** Mounted once in App.tsx. Renders whatever `confirmAction()` last requested.
 * Built on the existing Dialog primitive (not a separate AlertDialog package) —
 * `onPointerDownOutside`/`onEscapeKeyDown` are overridden so, like a native
 * alert dialog, it can only be closed by an explicit choice. */
export default function ConfirmDialogHost() {
  const { open, title, description, confirmLabel, variant } = useConfirmStore();

  return (
    <Dialog open={open} onOpenChange={(o) => !o && resolveConfirm(false)}>
      <DialogContent
        role="alertdialog"
        aria-describedby={description ? undefined : ""}
        onPointerDownOutside={(e) => e.preventDefault()}
        onEscapeKeyDown={(e) => {
          e.preventDefault();
          resolveConfirm(false);
        }}
      >
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description && <DialogDescription>{description}</DialogDescription>}
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => resolveConfirm(false)}>
            Cancel
          </Button>
          <Button variant={variant === "destructive" ? "destructive" : "default"} onClick={() => resolveConfirm(true)}>
            {confirmLabel || "Confirm"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
