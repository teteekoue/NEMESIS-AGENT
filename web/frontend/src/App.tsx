import * as React from "react";
import { Sidebar } from "@/components/Sidebar";
import { ChatHeader } from "@/components/ChatHeader";
import { MessageList } from "@/components/MessageList";
import { Composer } from "@/components/Composer";
import { RightPanel } from "@/components/RightPanel";
import { SettingsDialog } from "@/components/SettingsDialog";
import { AuthDialog } from "@/components/AuthDialog";
import { SessionModeDialog } from "@/components/SessionModeDialog";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useAppStore } from "@/store/useAppStore";
import { cn } from "@/lib/utils";

export default function App() {
  const bootstrap = useAppStore((s) => s.bootstrap);
  const messages = useAppStore((s) => s.messages);
  const sessionId = useAppStore((s) => s.sessionId);
  const [settingsOpen, setSettingsOpen] = React.useState(false);
  const [panelOpen, setPanelOpen] = React.useState(true);
  const [mobileNavOpen, setMobileNavOpen] = React.useState(false);
  const [draft, setDraft] = React.useState("");
  const [modeChosen, setModeChosen] = React.useState(false);
  const [modeOpen, setModeOpen] = React.useState(false);

  React.useEffect(() => {
    void bootstrap();
  }, [bootstrap]);

  const needsMode = messages.length === 0 && !sessionId;

  React.useEffect(() => {
    if (needsMode) {
      setModeChosen(false);
      setModeOpen(true);
    }
  }, [needsMode]);

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
            onOpenMode={() => setModeOpen(true)}
            onOpenNav={() => setMobileNavOpen(true)}
          />
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
        <SessionModeDialog
          open={modeOpen && !modeChosen}
          onOpenChange={(v) => {
            setModeOpen(v);
            if (!v) setModeChosen(true);
          }}
        />
      </div>
    </TooltipProvider>
  );
}
