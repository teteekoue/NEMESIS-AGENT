import * as React from "react";
import { FolderTree, Sparkles, Terminal, Wrench } from "lucide-react";
import { MessageBubble } from "@/components/MessageBubble";
import { BrandLogo } from "@/components/BrandLogo";
import { useAppStore } from "@/store/useAppStore";
import { useElapsed } from "@/hooks/useElapsed";
import { formatDuration } from "@/lib/utils";

const SUGGESTIONS = [
  { icon: Terminal, title: "Explorer le système", prompt: "Liste les fichiers du workspace et décris la structure du projet." },
  { icon: Wrench, title: "Créer un script", prompt: "Écris un script bash qui sauvegarde un dossier avec la date dans le nom." },
  { icon: FolderTree, title: "Analyser un projet", prompt: "Analyse le projet courant et propose une architecture de dossiers claire." },
  { icon: Sparkles, title: "Automatiser une tâche", prompt: "Propose et exécute une tâche d'administration Linux utile." },
];

function EmptyState({ onPick }: { onPick: (prompt: string) => void }) {
  return (
    <div className="flex h-full flex-col items-center justify-center px-6 text-center">
      <BrandLogo className="mb-5 h-16 w-16 rounded-2xl" />
      <h1 className="bg-gradient-to-r from-foreground to-muted-foreground bg-clip-text text-2xl font-semibold tracking-tight text-transparent">
        Bonjour, je suis NEMESIS
      </h1>
      <p className="mt-2 max-w-md text-sm text-muted-foreground">
        Votre agent autonome pour le code et l'administration système Linux. Décrivez une tâche,
        je choisis l'outil, je demande votre accord, puis j'exécute.
      </p>
      <div className="mt-8 grid w-full max-w-2xl grid-cols-1 gap-3 sm:grid-cols-2">
        {SUGGESTIONS.map((item) => (
          <button
            key={item.title}
            onClick={() => onPick(item.prompt)}
            className="group flex items-start gap-3 rounded-xl border border-border bg-card/50 p-3.5 text-left transition-all hover:border-primary/50 hover:bg-card hover:shadow-sm"
          >
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-secondary text-muted-foreground transition-colors group-hover:bg-primary/15 group-hover:text-primary">
              <item.icon className="h-4 w-4" />
            </div>
            <div className="min-w-0">
              <div className="text-sm font-medium text-foreground">{item.title}</div>
              <div className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">{item.prompt}</div>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

function ThinkingIndicator({ label }: { label: string }) {
  const turnStartedAt = useAppStore((s) => s.turnStartedAt);
  const elapsed = useElapsed(turnStartedAt, true);
  return (
    <div className="mx-auto flex w-full max-w-3xl items-start gap-3 px-4">
      <div className="relative mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-primary to-blue-700 shadow-sm">
        <span className="absolute h-7 w-7 animate-ping rounded-full bg-primary/25" />
        <Sparkles className="relative h-3.5 w-3.5 animate-pulse-soft text-primary-foreground" />
      </div>
      <div className="flex items-center gap-2 rounded-2xl rounded-tl-sm bg-card px-4 py-3 shadow-sm ring-1 ring-border">
        <span className="text-sm text-muted-foreground">{label}</span>
        <span className="flex gap-1">
          <span className="h-1.5 w-1.5 animate-pulse-soft rounded-full bg-primary [animation-delay:0ms]" />
          <span className="h-1.5 w-1.5 animate-pulse-soft rounded-full bg-primary [animation-delay:200ms]" />
          <span className="h-1.5 w-1.5 animate-pulse-soft rounded-full bg-primary [animation-delay:400ms]" />
        </span>
        <span className="ml-1 rounded bg-primary/10 px-1.5 py-0.5 font-mono text-[11px] tabular-nums text-primary">
          {formatDuration(elapsed)}
        </span>
      </div>
    </div>
  );
}

export function MessageList({ onSuggestion }: { onSuggestion: (prompt: string) => void }) {
  const messages = useAppStore((s) => s.messages);
  const statusText = useAppStore((s) => s.statusText);
  const busy = useAppStore((s) => s.busy);
  const scrollRef = React.useRef<HTMLDivElement>(null);
  const bottomRef = React.useRef<HTMLDivElement>(null);
  const pinnedRef = React.useRef(true);

  const handleScroll = React.useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    pinnedRef.current = distance < 120;
  }, []);

  React.useLayoutEffect(() => {
    if (!pinnedRef.current) return;
    bottomRef.current?.scrollIntoView({ behavior: "auto", block: "end" });
  }, [messages, statusText, busy]);

  if (messages.length === 0) {
    return <EmptyState onPick={onSuggestion} />;
  }

  return (
    <div ref={scrollRef} onScroll={handleScroll} className="scrollbar-thin h-full overflow-y-auto py-6">
      <div className="space-y-4">
        {messages.map((message) => (
          <MessageBubble key={message.id} message={message} />
        ))}
        {busy && <ThinkingIndicator label={statusText || "NEMESIS réfléchit"} />}
      </div>
      <div ref={bottomRef} />
    </div>
  );
}