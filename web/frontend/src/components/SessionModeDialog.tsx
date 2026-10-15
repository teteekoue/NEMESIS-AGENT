import * as React from "react";
import { ShieldCheck, ShieldQuestion } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useAppStore, type ApprovalMode } from "@/store/useAppStore";

const MODES: {
  id: ApprovalMode;
  title: string;
  description: string;
  icon: React.ComponentType<{ className?: string }>;
  accent: string;
}[] = [
  {
    id: "confirm",
    title: "Demander confirmation",
    description:
      "NEMESIS demande votre accord avant chaque action à risque (écriture, commande, suppression...). Les lectures restent automatiques.",
    icon: ShieldQuestion,
    accent: "text-amber-500",
  },
  {
    id: "all",
    title: "Toutes les autorisations",
    description:
      "Tous les appels d'outils sont exécutés sans accord préalable. Autonomie maximale — à utiliser en connaissance de cause.",
    icon: ShieldCheck,
    accent: "text-emerald-500",
  },
];

export function SessionModeDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const approvalMode = useAppStore((s) => s.approvalMode);
  const setApprovalMode = useAppStore((s) => s.setApprovalMode);
  const [selected, setSelected] = React.useState<ApprovalMode>(approvalMode);

  React.useEffect(() => {
    if (open) setSelected(approvalMode);
  }, [open, approvalMode]);

  const confirm = async () => {
    await setApprovalMode(selected);
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg" onInteractOutside={(e) => e.preventDefault()}>
        <DialogHeader>
          <DialogTitle>Choisir le mode de la session</DialogTitle>
          <DialogDescription>
            Ce réglage définit comment NEMESIS gère les autorisations pour cette session. Il reste modifiable à tout
            moment dans l'en-tête.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-2.5">
          {MODES.map((mode) => {
            const active = selected === mode.id;
            return (
              <button
                key={mode.id}
                type="button"
                onClick={() => setSelected(mode.id)}
                className={cn(
                  "flex items-start gap-3 rounded-xl border p-3.5 text-left transition-colors",
                  active ? "border-primary bg-primary/5" : "border-border hover:border-primary/40",
                )}
              >
                <div className={cn("mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-secondary", mode.accent)}>
                  <mode.icon className="h-4 w-4" />
                </div>
                <div className="min-w-0">
                  <div className="text-sm font-semibold">{mode.title}</div>
                  <div className="mt-0.5 text-xs leading-relaxed text-muted-foreground">{mode.description}</div>
                </div>
                <span
                  className={cn(
                    "ml-auto mt-0.5 h-4 w-4 shrink-0 rounded-full border-2",
                    active ? "border-primary bg-primary" : "border-border",
                  )}
                />
              </button>
            );
          })}
        </div>

        <DialogFooter>
          <Button onClick={confirm}>Démarrer avec ce mode</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}