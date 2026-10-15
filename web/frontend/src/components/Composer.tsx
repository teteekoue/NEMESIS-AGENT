import * as React from "react";
import { CornerDownLeft, Loader2, Send, Sparkles, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn, formatDuration } from "@/lib/utils";
import { useElapsed } from "@/hooks/useElapsed";
import { useAppStore } from "@/store/useAppStore";

export function Composer({
  draft,
  onDraftConsumed,
}: {
  draft?: string;
  onDraftConsumed?: () => void;
}) {
  const busy = useAppStore((s) => s.busy);
  const connected = useAppStore((s) => s.connected);
  const statusText = useAppStore((s) => s.statusText);
  const turnStartedAt = useAppStore((s) => s.turnStartedAt);
  const sendMessage = useAppStore((s) => s.sendMessage);
  const interrupt = useAppStore((s) => s.interrupt);
  const elapsed = useElapsed(turnStartedAt, busy);
  const [value, setValue] = React.useState("");
  const ref = React.useRef<HTMLTextAreaElement>(null);

  React.useEffect(() => {
    if (draft) {
      setValue(draft);
      onDraftConsumed?.();
      ref.current?.focus();
    }
  }, [draft, onDraftConsumed]);

  React.useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 220)}px`;
  }, [value]);

  const submit = () => {
    if (busy) return;
    const text = value.trim();
    if (!text) return;
    void sendMessage(text);
    setValue("");
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <div className="border-t border-border bg-background/80 px-4 py-3 backdrop-blur">
      <div className="mx-auto max-w-3xl">
        {/* Working indicator */}
        <div
          className={cn(
            "mb-2 flex items-center gap-2 overflow-hidden rounded-lg border px-3 transition-all",
            busy
              ? "max-h-12 border-primary/40 bg-primary/5 py-2 opacity-100"
              : "max-h-0 border-transparent py-0 opacity-0",
          )}
        >
          <div className="relative flex h-5 w-5 items-center justify-center">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary/30" />
<Sparkles className="relative h-3.5 w-3.5 animate-pulse-soft text-primary" />
          </div>
          <span className="text-xs font-medium text-primary">
            NEMESIS réfléchit{statusText ? ` · ${statusText}` : "..."}
          </span>
          <span className="ml-1 flex gap-1">
            <span className="h-1 w-1 animate-pulse-soft rounded-full bg-primary [animation-delay:0ms]" />
            <span className="h-1 w-1 animate-pulse-soft rounded-full bg-primary [animation-delay:200ms]" />
            <span className="h-1 w-1 animate-pulse-soft rounded-full bg-primary [animation-delay:400ms]" />
          </span>
          <span className="ml-auto rounded bg-primary/10 px-2 py-0.5 font-mono text-[11px] tabular-nums text-primary">
            {formatDuration(elapsed)}
          </span>
        </div>

        <div
          className={cn(
            "flex items-end gap-2 rounded-xl border bg-card p-2 shadow-sm transition-colors",
            busy
              ? "border-primary/50 ring-1 ring-primary/20"
              : "border-border focus-within:border-primary/60 focus-within:ring-1 focus-within:ring-primary/30",
            !connected && "opacity-60",
          )}
        >
          <textarea
            ref={ref}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={onKeyDown}
            rows={1}
            placeholder={
              !connected
                ? "Connexion à l'agent…"
                : busy
                  ? "NEMESIS travaille… (interrompre pour arrêter)"
                  : "Décrivez une tâche à NEMESIS…"
            }
            disabled={!connected}
            className="max-h-[220px] flex-1 resize-none bg-transparent px-2 py-1.5 text-sm outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed"
          />
          {busy ? (
            <Button variant="destructive" size="icon" onClick={interrupt} title="Interrompre">
              <Square className="h-4 w-4" />
            </Button>
          ) : (
            <Button size="icon" onClick={submit} disabled={!connected || !value.trim()} title="Envoyer">
              <Send className="h-4 w-4" />
            </Button>
          )}
        </div>
        <div className="mt-1.5 flex items-center justify-center gap-1.5 text-[11px] text-muted-foreground">
          {busy ? (
            <>
              <Loader2 className="h-3 w-3 animate-spin" />
              <span>L'agent traite votre demande…</span>
            </>
          ) : (
            <>
              <CornerDownLeft className="h-3 w-3" />
              <span>Entrée pour envoyer · Maj+Entrée pour un saut de ligne</span>
            </>
          )}
        </div>
      </div>
    </div>
  );
}