import * as React from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";
import { Sidebar } from "@/components/Sidebar";
import { ChatHeader } from "@/components/ChatHeader";
import { MessageList } from "@/components/MessageList";
import { Composer } from "@/components/Composer";
import { RightPanel } from "@/components/RightPanel";
import { SettingsDialog } from "@/components/SettingsDialog";
import { AuthDialog } from "@/components/AuthDialog";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Button } from "@/components/ui/button";
import { useAppStore } from "@/store/useAppStore";
import { cn } from "@/lib/utils";

export default function App() {
  const bootstrap = useAppStore((s) => s.bootstrap);
  const retryBootstrap = useAppStore((s) => s.retryBootstrap);
  const backendOffline = useAppStore((s) => s.backendOffline);
  const [settingsOpen, setSettingsOpen] = React.useState(false);
  const [panelOpen, setPanelOpen] = React.useState(true);
  const [mobileNavOpen, setMobileNavOpen] = React.useState(false);
  const [draft, setDraft] = React.useState("");

  React.useEffect(() => {
    void bootstrap();
  }, [bootstrap]);

  return (
    <TooltipProvider delayDuration={250}>
      <div className="app-gradient flex h-[100dvh] w-full overflow-hidden bg-background">
        <div className="hidden md:flex">
          <Sidebar onOpenSettings={() => setSettingsOpen(true)} />
        </div>

        {mobileNavOpen && (
          <div className="fixed inset-0 z-50 md:hidden">
            <div
              className="absolute inset-0 bg-foreground/40 backdrop-blur-sm"
              onClick={() => setMobileNavOpen(false)}
              aria-hidden
            />
            <div className="absolute inset-y-0 left-0 w-[85%] max-w-xs shadow-xl">
              <Sidebar
                onOpenSettings={() => {
                  setMobileNavOpen(false);
                  setSettingsOpen(true);
                }}
                onNavigate={() => setMobileNavOpen(false)}
              />
            </div>
          </div>
        )}

        <main className="flex min-w-0 flex-1 flex-col">
          <ChatHeader
            onTogglePanel={() => setPanelOpen((v) => !v)}
            onOpenNav={() => setMobileNavOpen(true)}
          />
          {backendOffline && (
            <div className="flex items-center gap-3 border-b border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive sm:px-4">
              <AlertTriangle className="h-4 w-4 shrink-0" />
              <span className="min-w-0 flex-1">
                Backend injoignable. Lancez le backend Web puis réessayez.
              </span>
              <Button
                variant="outline"
                size="sm"
                className="h-7 border-destructive/40 text-destructive hover:bg-destructive/10"
                onClick={() => void retryBootstrap()}
              >
                <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
                Réessayer
              </Button>
            </div>
          )}
          <div className="min-h-0 flex-1">
            <MessageList onSuggestion={(prompt) => setDraft(prompt)} />
          </div>
          <Composer draft={draft} onDraftConsumed={() => setDraft("")} />
        </main>

        <div className={cn("hidden lg:flex", !panelOpen && "lg:hidden")}>
          <RightPanel onClose={() => setPanelOpen(false)} />
        </div>

        <SettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} />
        <AuthDialog />
      </div>
    </TooltipProvider>
  );
}
