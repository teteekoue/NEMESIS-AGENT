import * as React from "react";
import {
  MessageSquarePlus,
  Moon,
  Search,
  Settings,
  Sun,
  Trash2,
} from "lucide-react";
import { BrandLogo } from "@/components/BrandLogo";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn, formatRelative, truncate } from "@/lib/utils";
import { useAppStore } from "@/store/useAppStore";

export function Sidebar({
  onOpenSettings,
  onNavigate,
}: {
  onOpenSettings: () => void;
  onNavigate?: () => void;
}) {
  const sessions = useAppStore((s) => s.sessions);
  const sessionId = useAppStore((s) => s.sessionId);
  const theme = useAppStore((s) => s.theme);
  const connected = useAppStore((s) => s.connected);
  const newSession = useAppStore((s) => s.newSession);
  const openSession = useAppStore((s) => s.openSession);
  const removeSession = useAppStore((s) => s.removeSession);
  const toggleTheme = useAppStore((s) => s.toggleTheme);
  const [query, setQuery] = React.useState("");

  const filtered = sessions.filter((s) =>
    s.title.toLowerCase().includes(query.toLowerCase()),
  );

  const gotoSession = (id: string) => {
    void openSession(id);
    onNavigate?.();
  };

  return (
    <aside className="flex h-full w-full flex-col border-r border-border bg-card/60 backdrop-blur md:w-72 md:shrink-0 md:bg-card/40">
      <div className="flex items-center gap-2.5 px-4 py-4">
        <BrandLogo className="h-9 w-9" />
        <div className="min-w-0">
          <div className="text-sm font-semibold leading-tight tracking-tight">NEMESIS</div>
          <div className="text-[11px] text-muted-foreground">Agent IA autonome</div>
        </div>
        <span
          className={cn(
            "ml-auto h-2 w-2 rounded-full",
            connected ? "bg-emerald-500" : "bg-muted-foreground/40",
          )}
          title={connected ? "Connecté" : "Hors ligne"}
        />
      </div>

      <div className="px-3">
        <Button
          className="w-full justify-start"
          onClick={() => {
            void newSession();
            onNavigate?.();
          }}
        >
          <MessageSquarePlus className="h-4 w-4" />
          Nouvelle conversation
        </Button>
      </div>

      <div className="px-3 pt-3">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Rechercher…"
            className="h-8 pl-8 text-xs"
          />
        </div>
      </div>

      <ScrollArea className="mt-2 flex-1">
        <div className="space-y-0.5 px-2 pb-2">
          {filtered.length === 0 && (
            <p className="px-3 py-6 text-center text-xs text-muted-foreground">
              Aucune conversation
            </p>
          )}
          {filtered.map((session) => (
            <div
              key={session.id}
              className={cn(
                "group flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-2 text-left transition-colors",
                sessionId === session.id ? "bg-secondary" : "hover:bg-secondary/60",
              )}
              onClick={() => gotoSession(session.id)}
            >
              <div className="min-w-0 flex-1">
                <div className="truncate text-xs font-medium">{truncate(session.title, 32)}</div>
                <div className="mt-0.5 flex items-center gap-1.5 text-[10px] text-muted-foreground">
                  <span>{formatRelative(session.updated_at)}</span>
                  <span>·</span>
                  <span>{session.message_count} msg</span>
                </div>
              </div>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  void removeSession(session.id);
                }}
                className="opacity-0 transition-opacity hover:text-destructive group-hover:opacity-100"
                title="Supprimer"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}
        </div>
      </ScrollArea>

      <div className="flex items-center gap-1 border-t border-border p-2">
        <Button variant="ghost" size="sm" className="flex-1 justify-start" onClick={onOpenSettings}>
          <Settings className="h-4 w-4" />
          Réglages
        </Button>
        <Button variant="ghost" size="icon-sm" onClick={toggleTheme} title="Thème">
          {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
        </Button>
      </div>
    </aside>
  );
}
