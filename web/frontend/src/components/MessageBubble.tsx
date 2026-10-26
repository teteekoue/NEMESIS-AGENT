import * as React from "react";
import { AlertTriangle, Info, Sparkles, Timer, User } from "lucide-react";
import { Markdown } from "@/components/Markdown";
import { ToolCallCard } from "@/components/ToolCallCard";
import { cn, formatDuration } from "@/lib/utils";
import type { ChatMessage } from "@/store/useAppStore";

function MessageBubbleInner({ message }: { message: ChatMessage }) {
  if (message.kind === "tool") {
    return (
      <div className="mx-auto w-full max-w-3xl px-4">
        <ToolCallCard message={message} />
      </div>
    );
  }

  if (message.kind === "notice") {
    return (
      <div className="mx-auto flex max-w-3xl items-center justify-center gap-2 px-4 py-1">
        <span className="inline-flex items-center gap-1.5 rounded-full bg-muted px-3 py-1 text-xs text-muted-foreground">
          <Info className="h-3 w-3" /> {message.text}
        </span>
      </div>
    );
  }

  if (message.kind === "error") {
    return (
      <div className="mx-auto w-full max-w-3xl px-4">
        <div className="flex items-start gap-2.5 rounded-lg border border-destructive/40 bg-destructive/10 px-3.5 py-2.5 text-sm text-destructive">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span className="whitespace-pre-wrap">{message.text}</span>
        </div>
      </div>
    );
  }

  if (message.kind === "user") {
    return (
      <div className="mx-auto flex w-full max-w-3xl justify-end px-4">
        <div className="flex max-w-[85%] items-start gap-2.5">
          <div className="rounded-2xl rounded-tr-sm bg-primary px-4 py-2.5 text-sm text-primary-foreground shadow-sm">
            {message.images && message.images.length > 0 && (
              <div className="mb-2 flex flex-wrap gap-1.5">
                {message.images.map((src, index) => (
                  <img
                    key={`${message.id}-img-${index}`}
                    src={src}
                    alt={`Pièce jointe ${index + 1}`}
                    className="h-20 w-20 rounded-lg border border-primary-foreground/20 object-cover"
                  />
                ))}
              </div>
            )}
            <p className="whitespace-pre-wrap break-words">{message.text}</p>
          </div>
          <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-secondary">
            <User className="h-3.5 w-3.5 text-secondary-foreground" />
          </div>
        </div>
      </div>
    );
  }

  // assistant
  return (
    <div className="mx-auto flex w-full max-w-3xl items-start gap-3 px-4">
      <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-primary to-blue-700 text-primary-foreground shadow-sm">
        <Sparkles className="h-3.5 w-3.5" />
      </div>
      <div className={cn("min-w-0 flex-1 rounded-2xl rounded-tl-sm bg-card px-4 py-3 shadow-sm ring-1 ring-border")}>
        <Markdown content={message.text || ""} />
        {typeof message.turnElapsed === "number" && (
          <div className="mt-2.5 flex items-center gap-2.5 border-t border-border/60 pt-2 text-[11px] text-muted-foreground">
            <span className="inline-flex items-center gap-1">
              <Timer className="h-3 w-3" />
              Tour terminé en <span className="font-mono tabular-nums text-foreground/80">{formatDuration(message.turnElapsed)}</span>
            </span>
            {typeof message.toolCount === "number" && message.toolCount > 0 && (
              <span className="inline-flex items-center gap-1">
                · <span className="font-mono tabular-nums text-foreground/80">{message.toolCount}</span> outil{message.toolCount > 1 ? "s" : ""}
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export const MessageBubble = React.memo(
  MessageBubbleInner,
  (prev, next) => prev.message === next.message,
);
