import { FolderTree } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { FileExplorer } from "@/components/FileExplorer";
import { TerminalPanel } from "@/components/TerminalPanel";
import { X } from "lucide-react";

export function RightPanel({ onClose }: { onClose: () => void }) {
  return (
    <div className="flex h-full w-80 shrink-0 flex-col border-l border-border bg-card/30">
      <Tabs defaultValue="files" className="flex h-full flex-col">
        <div className="flex items-center gap-1 border-b border-border px-2 py-2">
          <TabsList className="h-8 flex-1">
            <TabsTrigger value="files" className="text-xs">
              <FolderTree className="mr-1.5 h-3.5 w-3.5" /> Fichiers
            </TabsTrigger>
            <TabsTrigger value="terminal" className="text-xs">
              Terminal
            </TabsTrigger>
          </TabsList>
          <Button variant="ghost" size="icon-sm" onClick={onClose} title="Fermer">
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>
        <TabsContent value="files" className="m-0 flex-1 overflow-hidden">
          <FileExplorer />
        </TabsContent>
        <TabsContent value="terminal" className="m-0 flex-1 overflow-hidden">
          <TerminalPanel />
        </TabsContent>
      </Tabs>
    </div>
  );
}
