import * as React from "react";
import {
  CalendarClock,
  ChevronDown,
  CircleCheck,
  CircleX,
  FileCode2,
  FilePen,
  FilePlus2,
  FileSearch,
  FileText,
  FolderTree,
  GitBranch,
  Globe,
  LayoutList,
  ListChecks,
  Loader2,
  Plug,
  Puzzle,
  Search,
  ShieldAlert,
  ShieldQuestion,
  Ban,
  Sparkles,
  Terminal,
  Trash2,
  Wrench,
} from "lucide-react";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";
import { formatDuration } from "@/lib/utils";
import { useElapsed } from "@/hooks/useElapsed";
import type { ChatMessage } from "@/store/useAppStore";

type IconType = React.ComponentType<{ className?: string }>;

const TOOL_ICONS: Record<string, IconType> = {
  read_file: FileText,
  write_file: FilePlus2,
  edit: FilePen,
  apply_patch: FileCode2,
  delete_file: Trash2,
  list_dir: FolderTree,
  glob: FileSearch,
  grep: Search,
  bash: Terminal,
  git: GitBranch,
  web_search: Globe,
  web_fetch: Globe,
  mcp_list: Plug,
  mcp_call: Plug,
  mcp_tools_list: Plug,
  todo: ListChecks,
  list_agents: LayoutList,
  agent_status: CalendarClock,
  get_task_output: CalendarClock,
  kill_task: Trash2,
  delegate_task: Sparkles,
  skills_list: Puzzle,
};

const META_KEYS = new Set([
  "description",
  "timeout",
  "is_background",
  "mode",
  "replace_all",
  "dry_run",
  "offset",
  "limit",
  "case_insensitive",
  "max_results",
  "force",
]);

const COMMANDS: Record<string, string> = {
  read_file: "Lire",
  write_file: "Écrire",
  edit: "Modifier",
  apply_patch: "Patch",
  delete_file: "Supprimer",
  list_dir: "Lister",
  glob: "Glob",
  grep: "Rechercher",
  bash: "Exécuter",
  git: "Git",
  web_search: "Rechercher web",
  web_fetch: "Récupérer",
  mcp_list: "MCP",
  mcp_call: "Appeler MCP",
  mcp_tools_list: "Outils MCP",
  todo: "Planifier",
  list_agents: "Lister agents",
  agent_status: "Statut agent",
  get_task_output: "Sortie tâche",
  kill_task: "Arrêter tâche",
  delegate_task: "Déléguer",
  skills_list: "Compétences",
};

const PATH_KEYS = ["file_path", "path", "target_file", "filename"];
const TEXT_KEYS = ["content", "new_string", "old_string", "patch", "command"];
const QUERY_KEYS = ["pattern", "query", "url", "action", "content", "server", "tool"];

function firstString(params: Record<string, unknown>, keys: string[]): string | undefined {
  for (const key of keys) {
    const value = params[key];
    if (typeof value === "string" && value.trim()) return value;
    if (Array.isArray(value) && value.length && typeof value[0] === "string") return value[0];
  }
  return undefined;
}

function getArg(params: Record<string, unknown>): string {
  const path = firstString(params, PATH_KEYS);
  if (path) return path;
  const query = firstString(params, QUERY_KEYS);
  if (query) return query.replace(/\s+/g, " ").trim();
  const text = firstString(params, TEXT_KEYS);
  if (text) return text.replace(/\s+/g, " ").trim().slice(0, 80);
  return "";
}

function getExtraParams(params: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(params)) {
    if (META_KEYS.has(key)) continue;
    if (PATH_KEYS.includes(key) || QUERY_KEYS.includes(key) || TEXT_KEYS.includes(key)) continue;
    out[key] = value;
  }
  return out;
}

export function ToolCallCard({ message }: { message: ChatMessage }) {
  const [open, setOpen] = React.useState(false);
  const params = (message.params || {}) as Record<string, unknown>;
  const output = message.output || "";
  const Icon = TOOL_ICONS[message.name || ""] || Wrench;
  const command = COMMANDS[message.name || ""] || message.name || "outil";
  const arg = getArg(params);
  const hasDetails = Object.keys(params).length > 0 || Boolean(output);
  const extra = getExtraParams(params);
  const hasExtra = Object.keys(extra).length > 0;

  const running = message.status === "running";
  const liveSeconds = useElapsed(message.startedAt, running);
  const elapsedSeconds = running ? liveSeconds : message.duration ?? 0;
  const showTimer = running || typeof message.duration === "number";

  const statusIcon = () => {
    switch (message.status) {
      case "pending":
        return <ShieldQuestion className="h-3.5 w-3.5 text-amber-500" />;
      case "running":
        return <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />;
      case "denied":
        return <Ban className="h-3.5 w-3.5 text-destructive" />;
      default:
        return message.success ? (
          <CircleCheck className="h-3.5 w-3.5 text-emerald-500" />
        ) : (
          <CircleX className="h-3.5 w-3.5 text-destructive" />
        );
    }
  };

  const highRisk = message.risk === "high";

  return (
    <Collapsible open={open} onOpenChange={setOpen} className="animate-fade-in">
      <div
        className={cn(
          "group overflow-hidden rounded-lg border bg-card/50 transition-colors",
          message.status === "pending"
            ? "border-amber-500/50 bg-amber-500/5"
            : "border-border hover:border-primary/40",
        )}
      >
        <CollapsibleTrigger
          disabled={!hasDetails}
          className={cn(
            "flex w-full items-center gap-2.5 px-3 py-1.5 text-left",
            hasDetails && "cursor-pointer",
          )}
        >
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-secondary text-muted-foreground">
            <Icon className="h-3.5 w-3.5" />
          </span>
          <span className="shrink-0 text-xs font-medium text-foreground">{command}</span>
          {arg && (
            <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-muted-foreground">
              {arg}
            </span>
          )}
          {!arg && <span className="flex-1" />}
          {typeof message.exitCode === "number" && (
            <span
              className={cn(
                "shrink-0 rounded px-1.5 py-0.5 font-mono text-[10px] tabular-nums",
                message.exitCode === 0
                  ? "bg-emerald-500/10 text-emerald-500"
                  : "bg-destructive/10 text-destructive",
              )}
              title="Code de sortie"
            >
              exit {message.exitCode}
            </span>
          )}
          {showTimer && (
            <span
              className={cn(
                "shrink-0 rounded px-1.5 py-0.5 font-mono text-[10px] tabular-nums",
                running
                  ? "bg-primary/10 text-primary"
                  : "bg-secondary text-muted-foreground",
              )}
              title={running ? "Durée d'exécution en cours" : "Durée d'exécution"}
            >
              {formatDuration(elapsedSeconds)}
            </span>
          )}
          {message.status === "pending" && (
            <span className="shrink-0 text-[10px] font-medium text-amber-500">en attente</span>
          )}
          {highRisk && (
            <span className="shrink-0" title="Risque élevé">
              <ShieldAlert className="h-3.5 w-3.5 text-destructive" />
            </span>
          )}
          {statusIcon()}
          {hasDetails && (
            <ChevronDown
              className={cn(
                "h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform",
                open && "rotate-180",
              )}
            />
          )}
        </CollapsibleTrigger>

        {hasDetails && (
          <CollapsibleContent>
            <div className="space-y-2.5 border-t border-border/60 px-3 py-2.5">
              <div>
                <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                  Paramètres
                </div>
                <pre className="scrollbar-thin max-h-56 overflow-auto rounded-md border border-border/60 bg-[#0f172a] p-2.5 font-mono text-[11px] leading-relaxed text-slate-100">
                  {JSON.stringify(params, null, 2)}
                </pre>
              </div>
              {hasExtra && (
                <div className="flex flex-wrap gap-1.5">
                  {Object.entries(extra).map(([key, value]) => (
                    <span
                      key={key}
                      className="rounded bg-secondary px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground"
                    >
                      {key}={String(value).slice(0, 24)}
                    </span>
                  ))}
                </div>
              )}
              <div>
                <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                  Sortie
                </div>
                <pre className="scrollbar-thin max-h-72 overflow-auto whitespace-pre-wrap rounded-md border border-border/60 bg-[#0f172a] p-2.5 font-mono text-[11px] leading-relaxed text-slate-100">
                  {output || "(vide)"}
                </pre>
              </div>
            </div>
          </CollapsibleContent>
        )}
      </div>
    </Collapsible>
  );
}