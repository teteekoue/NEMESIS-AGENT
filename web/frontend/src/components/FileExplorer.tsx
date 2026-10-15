import * as React from "react";
import { ArrowLeft, ChevronRight, File as FileIcon, FileText, Folder, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn, formatBytes } from "@/lib/utils";
import { useAppStore } from "@/store/useAppStore";

export function FileExplorer() {
  const files = useAppStore((s) => s.files);
  const filePath = useAppStore((s) => s.filePath);
  const openFile = useAppStore((s) => s.openFile);
  const refreshFiles = useAppStore((s) => s.refreshFiles);
  const openWorkspaceFile = useAppStore((s) => s.openWorkspaceFile);
  const closeFile = useAppStore((s) => s.closeFile);

  const segments = filePath ? filePath.split("/") : [];

  if (openFile) {
    return (
      <div className="flex h-full flex-col">
        <div className="flex items-center gap-2 border-b border-border px-3 py-2">
          <Button variant="ghost" size="icon-sm" onClick={closeFile} title="Retour">
            <ArrowLeft className="h-3.5 w-3.5" />
          </Button>
          <FileText className="h-3.5 w-3.5 text-muted-foreground" />
          <span className="min-w-0 flex-1 truncate font-mono text-[11px]">{openFile.path}</span>
          <span className="text-[10px] text-muted-foreground">{formatBytes(openFile.size)}</span>
        </div>
        <ScrollArea className="flex-1">
          <pre className="scrollbar-thin whitespace-pre-wrap p-3 font-mono text-[11px] leading-relaxed text-foreground/90">
            {openFile.binary ? "[Fichier binaire — affichage partiel]\n\n" : ""}
            {openFile.content}
            {openFile.truncated ? "\n\n… [fichier tronqué]" : ""}
          </pre>
        </ScrollArea>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-1 border-b border-border px-3 py-2">
        <Button
          variant="ghost"
          size="icon-sm"
          disabled={!filePath}
          onClick={() => {
            const parent = segments.slice(0, -1).join("/");
            void refreshFiles(parent);
          }}
          title="Dossier parent"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
        </Button>
        <div className="flex min-w-0 flex-1 items-center gap-0.5 overflow-hidden text-[11px] text-muted-foreground">
          <button className="shrink-0 hover:text-foreground" onClick={() => void refreshFiles("")}>
            workspace
          </button>
          {segments.map((segment, index) => (
            <React.Fragment key={index}>
              <ChevronRight className="h-3 w-3 shrink-0" />
              <button
                className="truncate hover:text-foreground"
                onClick={() => void refreshFiles(segments.slice(0, index + 1).join("/"))}
              >
                {segment}
              </button>
            </React.Fragment>
          ))}
        </div>
        <Button variant="ghost" size="icon-sm" onClick={() => void refreshFiles()} title="Rafraîchir">
          <RefreshCw className="h-3.5 w-3.5" />
        </Button>
      </div>

      <ScrollArea className="flex-1">
        <div className="p-1.5">
          {files?.entries.length === 0 && (
            <p className="px-3 py-6 text-center text-xs text-muted-foreground">Dossier vide</p>
          )}
          {files?.entries.map((entry) => (
            <button
              key={entry.path}
              onClick={() =>
                entry.is_dir ? void refreshFiles(entry.path) : void openWorkspaceFile(entry.path)
              }
              className={cn(
                "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-secondary/70",
              )}
            >
              {entry.is_dir ? (
                <Folder className="h-3.5 w-3.5 shrink-0 text-primary" />
              ) : (
                <FileIcon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              )}
              <span className="min-w-0 flex-1 truncate text-xs">{entry.name}</span>
              {!entry.is_dir && (
                <span className="shrink-0 text-[10px] text-muted-foreground">
                  {formatBytes(entry.size)}
                </span>
              )}
            </button>
          ))}
        </div>
      </ScrollArea>
    </div>
  );
}
