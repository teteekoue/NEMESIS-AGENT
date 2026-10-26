import * as React from "react";
import { CornerDownLeft, Loader2, Paperclip, Send, Sparkles, Square, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn, formatDuration } from "@/lib/utils";
import { useElapsed } from "@/hooks/useElapsed";
import { useAppStore } from "@/store/useAppStore";

const MAX_IMAGES = 8;
const MAX_IMAGE_BYTES = 8 * 1024 * 1024;

interface Attachment {
  id: string;
  name: string;
  url: string;
}

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
  const [attachments, setAttachments] = React.useState<Attachment[]>([]);
  const [attachError, setAttachError] = React.useState("");
  const ref = React.useRef<HTMLTextAreaElement>(null);
  const fileRef = React.useRef<HTMLInputElement>(null);

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

  const onPickFiles = (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files || []);
    event.target.value = "";
    if (!files.length) return;
    setAttachError("");
    const room = MAX_IMAGES - attachments.length;
    if (room <= 0) {
      setAttachError(`Maximum ${MAX_IMAGES} images par message.`);
      return;
    }
    let skipped = 0;
    const selected = files.slice(0, room);
    if (files.length > room) skipped += files.length - room;
    selected.forEach((file) => {
      if (!file.type.startsWith("image/") || file.size > MAX_IMAGE_BYTES) {
        skipped += 1;
        return;
      }
      const reader = new FileReader();
      reader.onload = () => {
        if (typeof reader.result !== "string") return;
        setAttachments((current) => {
          if (current.length >= MAX_IMAGES) return current;
          return [
            ...current,
            {
              id: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
              name: file.name,
              url: reader.result as string,
            },
          ];
        });
      };
      reader.readAsDataURL(file);
    });
    if (skipped) {
      setAttachError(
        `${skipped} fichier(s) ignoré(s) : formats image uniquement, 8 Mo maximum, ${MAX_IMAGES} images par message.`,
      );
    }
  };

  const submit = () => {
    if (busy || !connected) return;
    const text = value.trim();
    if (!text && attachments.length === 0) return;
    void sendMessage(text, attachments.map((item) => item.url));
    setValue("");
    setAttachments([]);
    setAttachError("");
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

        {attachments.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-2">
            {attachments.map((item) => (
              <div
                key={item.id}
                className="group relative h-16 w-16 overflow-hidden rounded-lg border border-border bg-muted"
                title={item.name}
              >
                <img src={item.url} alt={item.name} className="h-full w-full object-cover" />
                <button
                  type="button"
                  onClick={() => setAttachments((current) => current.filter((a) => a.id !== item.id))}
                  className="absolute right-0.5 top-0.5 rounded-full bg-background/90 p-0.5 text-foreground shadow-sm hover:bg-destructive hover:text-destructive-foreground"
                  title="Retirer l'image"
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            ))}
            <span className="self-end pb-1 text-[11px] text-muted-foreground">
              {attachments.length}/{MAX_IMAGES} image{attachments.length > 1 ? "s" : ""} · envoyées en multimodal
            </span>
          </div>
        )}
        {attachError && <p className="mb-2 text-[11px] text-destructive">{attachError}</p>}

        <div
          className={cn(
            "flex items-end gap-2 rounded-xl border bg-card p-2 shadow-sm transition-colors",
            busy
              ? "border-primary/50 ring-1 ring-primary/20"
              : "border-border focus-within:border-primary/60 focus-within:ring-1 focus-within:ring-primary/30",
            !connected && "opacity-60",
          )}
        >
          <input
            ref={fileRef}
            type="file"
            accept="image/*"
            multiple
            className="hidden"
            onChange={onPickFiles}
          />
          <Button
            variant="ghost"
            size="icon"
            onClick={() => fileRef.current?.click()}
            disabled={!connected || busy || attachments.length >= MAX_IMAGES}
            title="Joindre une image (multimodal)"
            className="shrink-0"
          >
            <Paperclip className="h-4 w-4" />
          </Button>
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
                  : "Décrivez une tâche à NEMESIS… (trombone pour joindre une image)"
            }
            disabled={!connected}
            className="max-h-[220px] flex-1 resize-none bg-transparent px-2 py-1.5 text-sm outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed"
          />
          {busy ? (
            <Button variant="destructive" size="icon" onClick={interrupt} title="Interrompre">
              <Square className="h-4 w-4" />
            </Button>
          ) : (
            <Button
              size="icon"
              onClick={submit}
              disabled={!connected || (!value.trim() && attachments.length === 0)}
              title="Envoyer"
            >
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
              <span>Entrée pour envoyer · Maj+Entrée pour un saut de ligne · images via le trombone</span>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
