import * as React from "react";
import { Check, ShieldAlert, X } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useAppStore } from "@/store/useAppStore";

export function AuthDialog() {
  const pending = useAppStore((s) => s.pendingAuth);
  const authorize = useAppStore((s) => s.authorize);

  React.useEffect(() => {
    if (!pending) return;
    const onKey = (event: KeyboardEvent) => {
      const key = event.key.toLowerCase();
      if (key === "y") authorize("y");
      if (key === "n") authorize("n");
      if (key === "a") authorize("a");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pending, authorize]);

  if (!pending) return null;

  const riskVariant =
    pending.risk === "high" ? "destructive" : pending.risk === "read" ? "secondary" : "warning";

  return (
    <Dialog open={Boolean(pending)}>
      <DialogContent
        className="flex max-h-[85vh] max-w-xl flex-col gap-3 overflow-hidden"
        onEscapeKeyDown={(e) => e.preventDefault()}
        onInteractOutside={(e) => e.preventDefault()}
        onOpenAutoFocus={(e) => e.preventDefault()}
      >
        <DialogHeader className="shrink-0">
          <div className="flex items-start gap-2.5">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-amber-500/15 text-amber-500">
              <ShieldAlert className="h-5 w-5" />
            </div>
            <div className="min-w-0">
              <DialogTitle>Autorisation requise</DialogTitle>
              <DialogDescription>
                NEMESIS souhaite exécuter un outil. Validez l'action avant exécution.
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>

        <div className="min-h-0 flex-1 space-y-2 overflow-hidden">
          <div className="flex flex-wrap items-center gap-2">
            <span className="break-all font-mono text-sm font-semibold">{pending.name}</span>
            {pending.risk && (
              <Badge variant={riskVariant as "destructive" | "warning" | "secondary"}>
                {pending.risk}
              </Badge>
            )}
          </div>
          <pre className="scrollbar-thin max-h-52 overflow-auto whitespace-pre-wrap break-all rounded-lg border border-border bg-[#0f172a] p-3 font-mono text-xs leading-relaxed text-slate-100">
            {JSON.stringify(pending.params || {}, null, 2)}
          </pre>
        </div>

        <DialogFooter className="shrink-0 flex-col gap-2 sm:flex-row sm:gap-2">
          <Button variant="outline" className="w-full sm:w-auto" onClick={() => authorize("n")}>
            <X className="h-4 w-4" />
            Refuser
            <kbd className="rounded bg-muted px-1 text-[10px]">N</kbd>
          </Button>
          <Button variant="secondary" className="w-full sm:w-auto" onClick={() => authorize("a")}>
            Toujours autoriser
            <kbd className="rounded bg-muted px-1 text-[10px]">A</kbd>
          </Button>
          <Button className="w-full sm:w-auto" onClick={() => authorize("y")}>
            <Check className="h-4 w-4" />
            Autoriser
            <kbd className="rounded bg-white/20 px-1 text-[10px]">Y</kbd>
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}