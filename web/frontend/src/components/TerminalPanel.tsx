import * as React from "react";
import { Ban, Eraser, Loader2, RotateCw, ScrollText, TerminalSquare } from "lucide-react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useAppStore } from "@/store/useAppStore";

type Mode = "shell" | "log";

const THEME = {
  background: "#08080c",
  foreground: "#d4d4d8",
  cursor: "#a78bfa",
  selectionBackground: "#3b2d63",
  black: "#18181b",
  red: "#f87171",
  green: "#4ade80",
  yellow: "#facc15",
  blue: "#60a5fa",
  magenta: "#c084fc",
  cyan: "#22d3ee",
  white: "#e4e4e7",
  brightBlack: "#52525b",
};

function RealTerminal() {
  const containerRef = React.useRef<HTMLDivElement>(null);
  const termRef = React.useRef<Terminal | null>(null);
  const fitRef = React.useRef<FitAddon | null>(null);
  const socketRef = React.useRef<WebSocket | null>(null);
  const [status, setStatus] = React.useState<"connecting" | "open" | "closed">("connecting");

  const connect = React.useCallback(() => {
    setStatus("connecting");
    const cols = termRef.current?.cols || 120;
    const rows = termRef.current?.rows || 30;
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const url = `${proto}://${window.location.host}/ws/terminal?cols=${cols}&rows=${rows}`;
    const ws = new WebSocket(url);
    socketRef.current = ws;

    ws.onopen = () => setStatus("open");
    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === "output") {
          termRef.current?.write(msg.data);
        } else if (msg.type === "ready") {
          termRef.current?.write(`\x1b[38;5;141m● Terminal NEMESIS — ${msg.shell}\x1b[0m\r\n`);
        } else if (msg.type === "error") {
          termRef.current?.write(`\r\n\x1b[31m${msg.message}\x1b[0m\r\n`);
        } else if (msg.type === "exit") {
          setStatus("closed");
          termRef.current?.write("\r\n\x1b[38;5;244m[processus terminé]\x1b[0m\r\n");
        }
      } catch {
        /* ignore */
      }
    };
    ws.onclose = () => {
      setStatus("closed");
      socketRef.current = null;
    };
    ws.onerror = () => setStatus("closed");
  }, []);

  React.useEffect(() => {
    if (!containerRef.current) return;
    const term = new Terminal({
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
      fontSize: 12.5,
      cursorBlink: true,
      convertEol: false,
      theme: THEME,
      scrollback: 5000,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(containerRef.current);
    fitRef.current = fit;
    termRef.current = term;
    try {
      fit.fit();
    } catch {
      /* ignore */
    }

    const dataSub = term.onData((data) => {
      const ws = socketRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "input", data }));
      }
    });

    connect();

    const onResize = () => {
      try {
        fit.fit();
      } catch {
        /* ignore */
      }
      const ws = socketRef.current;
      if (ws && ws.readyState === WebSocket.OPEN && termRef.current) {
        ws.send(JSON.stringify({ type: "resize", cols: termRef.current.cols, rows: termRef.current.rows }));
      }
    };
    window.addEventListener("resize", onResize);
    const observer = new ResizeObserver(onResize);
    observer.observe(containerRef.current);

    return () => {
      window.removeEventListener("resize", onResize);
      observer.disconnect();
      dataSub.dispose();
      socketRef.current?.close();
      term.dispose();
    };
  }, [connect]);

  const reconnect = () => {
    socketRef.current?.close();
    termRef.current?.reset();
    connect();
  };

  const clear = () => termRef.current?.clear();

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-border px-3 py-1.5">
        <span className="flex items-center gap-1.5 font-mono text-[11px] text-muted-foreground">
          <span
            className={cn(
              "h-1.5 w-1.5 rounded-full",
              status === "open" ? "bg-emerald-500" : status === "connecting" ? "bg-amber-500" : "bg-red-500",
            )}
          />
          shell réel {status === "open" ? "connecté" : status === "connecting" ? "…" : "déconnecté"}
        </span>
        <div className="flex items-center gap-0.5">
          <Button variant="ghost" size="icon-sm" onClick={clear} title="Effacer l'écran">
            <Eraser className="h-3.5 w-3.5" />
          </Button>
          <Button variant="ghost" size="icon-sm" onClick={reconnect} title="Reconnecter">
            <RotateCw className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>
      <div ref={containerRef} className="min-h-0 flex-1 overflow-hidden bg-[#08080c] p-1" />
    </div>
  );
}

function LogConsole() {
  const logs = useAppStore((s) => s.logs);
  const clearLogs = useAppStore((s) => s.clearLogs);
  const bottomRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [logs]);

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-border px-3 py-1.5">
        <span className="font-mono text-[11px] text-muted-foreground">journal d'exécution NEMESIS</span>
        <Button variant="ghost" size="icon-sm" onClick={clearLogs} title="Effacer">
          <Eraser className="h-3.5 w-3.5" />
        </Button>
      </div>
      <div className="scrollbar-thin min-h-0 flex-1 overflow-auto bg-[#08080c] p-3 font-mono text-[11px] leading-relaxed">
        {logs.length === 0 ? (
          <p className="text-muted-foreground">Aucune activité pour le moment.</p>
        ) : (
          logs.map((line, index) => (
            <div
              key={index}
              className={cn(
                "whitespace-pre-wrap break-all",
                line.includes("✗") || line.includes("!")
                  ? "text-red-400"
                  : line.includes("✓")
                    ? "text-emerald-400"
                    : line.includes("→")
                      ? "text-sky-300"
                      : line.includes("⨯")
                        ? "text-amber-400"
                        : "text-zinc-300",
              )}
            >
              {line}
            </div>
          ))
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}

export function TerminalPanel() {
  const [mode, setMode] = React.useState<Mode>("shell");

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-1 border-b border-border px-2 py-1.5">
        <button
          type="button"
          onClick={() => setMode("shell")}
          className={cn(
            "flex items-center gap-1 rounded px-2 py-1 text-[11px] font-medium transition-colors",
            mode === "shell" ? "bg-primary/15 text-primary" : "text-muted-foreground hover:bg-secondary",
          )}
        >
          <TerminalSquare className="h-3 w-3" /> Terminal
        </button>
        <button
          type="button"
          onClick={() => setMode("log")}
          className={cn(
            "flex items-center gap-1 rounded px-2 py-1 text-[11px] font-medium transition-colors",
            mode === "log" ? "bg-primary/15 text-primary" : "text-muted-foreground hover:bg-secondary",
          )}
        >
          <ScrollText className="h-3 w-3" /> Journal
        </button>
      </div>
      <div className="min-h-0 flex-1">{mode === "shell" ? <RealTerminal /> : <LogConsole />}</div>
    </div>
  );
}