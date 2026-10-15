import {
  Cpu,
  Menu,
  PanelRight,
  Plug,
  PlugZap,
  ShieldCheck,
  ShieldQuestion,
  WifiOff,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { useAppStore } from "@/store/useAppStore";

export function ChatHeader({
  onTogglePanel,
  onOpenMode,
  onOpenNav,
}: {
  onTogglePanel: () => void;
  onOpenMode: () => void;
  onOpenNav: () => void;
}) {
  const config = useAppStore((s) => s.config);
  const models = useAppStore((s) => s.models);
  const provider = useAppStore((s) => s.provider);
  const agentReachable = useAppStore((s) => s.agentReachable);
  const connected = useAppStore((s) => s.connected);
  const tools = useAppStore((s) => s.tools);
  const selectModel = useAppStore((s) => s.selectModel);
  const approvalMode = useAppStore((s) => s.approvalMode);

  const currentModel = config?.provider?.model || provider?.model || "";
  const modelOptions = models?.models?.length
    ? models.models.map((m) => m.id)
    : currentModel
      ? [currentModel]
      : [];

  const status = !connected
    ? { label: "Hors ligne", variant: "secondary" as const, icon: WifiOff, color: "text-muted-foreground" }
    : agentReachable === false
      ? { label: "NEMAPI injoignable", variant: "destructive" as const, icon: Plug, color: "text-destructive" }
      : { label: "Connecté", variant: "success" as const, icon: PlugZap, color: "text-emerald-500" };

  const modeIsAuto = approvalMode === "all";

  return (
    <header className="flex h-14 shrink-0 items-center gap-2 border-b border-border bg-background/80 px-2 backdrop-blur sm:gap-3 sm:px-4">
      <Button
        variant="ghost"
        size="icon"
        className="md:hidden"
        onClick={onOpenNav}
        title="Menu"
      >
        <Menu className="h-4 w-4" />
      </Button>

      <div className="flex items-center gap-2">
        <Select value={currentModel} onValueChange={(value) => void selectModel(value)}>
          <SelectTrigger className="h-9 w-[130px] gap-2 text-xs sm:w-[180px]">
            <Cpu className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            <SelectValue placeholder="Modèle" />
          </SelectTrigger>
          <SelectContent>
            {modelOptions.map((id) => (
              <SelectItem key={id} value={id}>
                {id}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <TooltipProvider delayDuration={200}>
        <Tooltip>
          <TooltipTrigger asChild>
            <button type="button" onClick={onOpenMode}>
              <Badge
                variant={modeIsAuto ? "success" : "warning"}
                className="gap-1.5 cursor-pointer transition-opacity hover:opacity-80"
              >
                {modeIsAuto ? (
                  <ShieldCheck className="h-3 w-3" />
                ) : (
                  <ShieldQuestion className="h-3 w-3" />
                )}
                {modeIsAuto ? "Toutes autorisations" : "Confirmation"}
              </Badge>
            </button>
          </TooltipTrigger>
          <TooltipContent>Cliquer pour changer le mode d'autorisation de la session</TooltipContent>
        </Tooltip>
      </TooltipProvider>

      <TooltipProvider delayDuration={200}>
        <Tooltip>
          <TooltipTrigger asChild>
            <Badge variant={status.variant} className="hidden gap-1.5 sm:inline-flex">
              <status.icon className={cn("h-3 w-3", status.color)} />
              {status.label}
            </Badge>
          </TooltipTrigger>
          <TooltipContent>
            {provider ? `${provider.type} · ${provider.host}:${provider.port}` : "Aucun provider"}
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>

      <span className="hidden text-[11px] text-muted-foreground sm:inline">
        {tools.length} outils · {config?.workspace || "workspace"}
      </span>

      <div className="ml-auto flex items-center gap-1">
        <Button variant="ghost" size="icon" onClick={onTogglePanel} title="Panneau latéral">
          <PanelRight className="h-4 w-4" />
        </Button>
      </div>
    </header>
  );
}