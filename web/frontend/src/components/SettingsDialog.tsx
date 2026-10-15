import * as React from "react";
import {
  Brain,
  Check,
  Cloud,
  FolderTree,
  Github,
  Loader2,
  Package,
  Plug,
  Plus,
  RefreshCw,
  Save,
  Send,
  Server,
  SlidersHorizontal,
  Trash2,
  TriangleAlert,
  Workflow,
} from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { toast } from "sonner";
import { useAppStore } from "@/store/useAppStore";
import { api } from "@/lib/api";

type Mode = "local" | "url";

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <label className="text-xs font-medium text-muted-foreground">{label}</label>
      {children}
      {hint && <div className="text-[11px] leading-relaxed text-muted-foreground">{hint}</div>}
    </div>
  );
}

const QUICK_WORKSPACES = ["~/nemesis-workspace", "~/", "/tmp", "."];

export function SettingsDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const config = useAppStore((s) => s.config);
  const models = useAppStore((s) => s.models);
  const tools = useAppStore((s) => s.tools);
  const saveConfig = useAppStore((s) => s.saveConfig);
  const refreshModels = useAppStore((s) => s.refreshModels);
  const reconnectAgent = useAppStore((s) => s.reconnectAgent);
  const mcpServers = useAppStore((s) => s.mcpServers);
  const skills = useAppStore((s) => s.skills);
  const refreshMcp = useAppStore((s) => s.refreshMcp);
  const refreshSkills = useAppStore((s) => s.refreshSkills);
  const memoryBlocks = useAppStore((s) => s.memoryBlocks);
  const memoryEnabled = useAppStore((s) => s.memoryEnabled);
  const refreshMemory = useAppStore((s) => s.refreshMemory);
  const upsertMemory = useAppStore((s) => s.upsertMemory);
  const deleteMemory = useAppStore((s) => s.deleteMemory);
  const clearMemory = useAppStore((s) => s.clearMemory);
  const toggleMemory = useAppStore((s) => s.toggleMemory);
  const telegram = useAppStore((s) => s.telegram);
  const refreshTelegram = useAppStore((s) => s.refreshTelegram);
  const saveTelegram = useAppStore((s) => s.saveTelegram);
  const github = useAppStore((s) => s.github);
  const refreshGithub = useAppStore((s) => s.refreshGithub);
  const saveGithub = useAppStore((s) => s.saveGithub);

  const [mode, setMode] = React.useState<Mode>("local");
  const [host, setHost] = React.useState("127.0.0.1");
  const [port, setPort] = React.useState("8090");
  const [url, setUrl] = React.useState("");
  const [model, setModel] = React.useState("");
  const [workspace, setWorkspace] = React.useState("");
  const [autoApproveReads, setAutoApproveReads] = React.useState(true);
  const [confirmHighRisk, setConfirmHighRisk] = React.useState(true);
  const [showOutputs, setShowOutputs] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const [testing, setTesting] = React.useState(false);
  const [query, setQuery] = React.useState("");

  const [mcpName, setMcpName] = React.useState("");
  const [mcpCommand, setMcpCommand] = React.useState("");
  const [mcpArgs, setMcpArgs] = React.useState("");
  const [mcpDesc, setMcpDesc] = React.useState("");
  const [mcpBusy, setMcpBusy] = React.useState(false);

  const [memTitle, setMemTitle] = React.useState("");
  const [memContent, setMemContent] = React.useState("");
  const [memTags, setMemTags] = React.useState("");
  const [memEditId, setMemEditId] = React.useState<string | null>(null);
  const [memBusy, setMemBusy] = React.useState(false);

  const [tgToken, setTgToken] = React.useState("");
  const [tgBotName, setTgBotName] = React.useState("");
  const [tgBusy, setTgBusy] = React.useState(false);

  const [ghToken, setGhToken] = React.useState("");
  const [ghName, setGhName] = React.useState("");
  const [ghEmail, setGhEmail] = React.useState("");
  const [ghLogin, setGhLogin] = React.useState("");
  const [ghBusy, setGhBusy] = React.useState(false);

  React.useEffect(() => {
    if (!open) return;
    setMode(config?.mode === "url" ? "url" : "local");
    setHost(config?.nemapi?.host || "127.0.0.1");
    setPort(String(config?.nemapi?.port ?? 8090));
    setUrl(config?.nemapi?.url || "");
    setModel(config?.provider?.model || config?.model || "");
    setWorkspace(config?.security?.workspace || config?.workspace || "");
    setAutoApproveReads(config?.security?.auto_approve_reads ?? true);
    setConfirmHighRisk(config?.security?.confirm_high_risk ?? true);
    setShowOutputs(config?.security?.show_tool_outputs ?? false);
    void refreshMcp();
    void refreshSkills();
    void refreshMemory();
    void refreshTelegram();
    void refreshGithub();
    setTgBotName("");
    setTgToken("");
  }, [open, config, refreshMcp, refreshSkills, refreshMemory, refreshTelegram, refreshGithub]);

  React.useEffect(() => {
    if (!github) return;
    setGhName(github.name || "");
    setGhEmail(github.email || "");
    setGhLogin(github.login || "");
    setGhToken("");
  }, [github]);

  const filteredTools = tools.filter(
    (tool) =>
      tool.name.toLowerCase().includes(query.toLowerCase()) ||
      tool.description.toLowerCase().includes(query.toLowerCase()),
  );

  const normalizedUrl = React.useMemo(() => {
    const raw = url.trim();
    if (!raw) return "";
    return /^https?:\/\//i.test(raw) ? raw : `https://${raw}`;
  }, [url]);

  const applyConnection = async (closeAfter: boolean) => {
    const nemapi: Record<string, unknown> = {};
    if (mode === "url") {
      if (!normalizedUrl) {
        toast.error("Saisissez l'URL publique du serveur NEMAPI.");
        return false;
      }
      nemapi.url = normalizedUrl;
      nemapi.host = "";
    } else {
      nemapi.url = "";
      nemapi.host = host.trim() || "127.0.0.1";
      nemapi.port = Number(port) || 8090;
    }
    const ok = await saveConfig({
      nemapi,
      provider: { model: model.trim() },
      security: {
        workspace,
        auto_approve_reads: autoApproveReads,
        confirm_high_risk: confirmHighRisk,
        show_tool_outputs: showOutputs,
      },
    } as any);
    if (ok) {
      reconnectAgent();
      if (closeAfter) onOpenChange(false);
    }
    return ok;
  };

  const testConnection = async () => {
    setTesting(true);
    await applyConnection(false);
    await refreshModels();
    setTesting(false);
    const state = useAppStore.getState();
    const count = state.models?.models.length || 0;
    if (state.agentReachable && count > 0) {
      toast.success(`Connecté — ${count} modèle(s) détecté(s).`);
    } else if (state.agentReachable) {
      toast.warning("Connecté, mais aucun modèle détecté. Saisissez l'identifiant manuellement.");
    } else {
      toast.error("Serveur injoignable. Vérifiez l'URL ou l'hôte/port.");
    }
  };

  const save = async () => {
    setSaving(true);
    const ok = await applyConnection(true);
    setSaving(false);
    toast[ok ? "success" : "error"](ok ? "Configuration enregistrée" : "Échec de l'enregistrement");
  };

  const addMcp = async () => {
    if (!mcpName.trim() || !mcpCommand.trim()) {
      toast.error("Nom et commande requis.");
      return;
    }
    setMcpBusy(true);
    try {
      await api.addMcpServer({
        name: mcpName.trim(),
        command: mcpCommand.trim(),
        args: mcpArgs.trim() ? mcpArgs.trim().split(/\s+/) : undefined,
        description: mcpDesc.trim(),
      });
      setMcpName("");
      setMcpCommand("");
      setMcpArgs("");
      setMcpDesc("");
      await refreshMcp();
      toast.success("Serveur MCP ajouté.");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Échec de l'ajout.");
    } finally {
      setMcpBusy(false);
    }
  };

  const removeMcp = async (name: string) => {
    try {
      await api.removeMcpServer(name);
      await refreshMcp();
      toast.success(`Serveur '${name}' supprimé.`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Échec de la suppression.");
    }
  };

  const resetMemForm = () => {
    setMemTitle("");
    setMemContent("");
    setMemTags("");
    setMemEditId(null);
  };

  const saveMemoryBlock = async () => {
    if (!memTitle.trim() || !memContent.trim()) {
      toast.error("Titre et contenu requis.");
      return;
    }
    setMemBusy(true);
    try {
      await upsertMemory({
        id: memEditId ?? undefined,
        title: memTitle.trim(),
        content: memContent.trim(),
        tags: memTags.trim() ? memTags.split(",").map((t) => t.trim()).filter(Boolean) : [],
      });
      resetMemForm();
      toast.success(memEditId ? "Bloc mis à jour." : "Bloc ajouté.");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Échec de l'enregistrement.");
    } finally {
      setMemBusy(false);
    }
  };

  const editMemoryBlock = (block: (typeof memoryBlocks)[number]) => {
    setMemEditId(block.id);
    setMemTitle(block.title);
    setMemContent(block.content);
    setMemTags((block.tags || []).join(", "));
  };

  const removeMemoryBlock = async (id: string) => {
    try {
      await deleteMemory(id);
      if (memEditId === id) resetMemForm();
      toast.success("Bloc supprimé.");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Échec de la suppression.");
    }
  };

  const clearMemoryAll = async () => {
    try {
      await clearMemory();
      resetMemForm();
      toast.success("Mémoire vidée.");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Échec.");
    }
  };

  const changeMemoryToggle = async (enabled: boolean) => {
    try {
      await toggleMemory(enabled);
      toast.success(enabled ? "Mémoire activée." : "Mémoire désactivée.");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Échec.");
    }
  };

  const saveTelegramConfig = async () => {
    if (!tgToken.trim() && !tgBotName.trim()) {
      toast.error("Saisissez le token du bot (BotFather).");
      return;
    }
    setTgBusy(true);
    try {
      await saveTelegram({
        token: tgToken.trim() || undefined,
        bot_name: tgBotName.trim() || undefined,
      });
      setTgToken("");
      toast.success("Configuration Telegram enregistrée.");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Échec de l'enregistrement.");
    } finally {
      setTgBusy(false);
    }
  };

  const saveGithubConfig = async () => {
    const hasToken = ghToken.trim().length > 0;
    if (!hasToken && !github?.token_set && !ghName.trim() && !ghEmail.trim()) {
      toast.error("Saisissez un token GitHub (PAT) pour connecter l'agent.");
      return;
    }
    setGhBusy(true);
    try {
      await saveGithub({
        token: hasToken ? ghToken.trim() : undefined,
        name: ghName.trim(),
        email: ghEmail.trim(),
        login: ghLogin.trim(),
      });
      setGhToken("");
      await refreshMcp();
      toast.success("Connexion GitHub enregistrée.");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Échec de l'enregistrement.");
    } finally {
      setGhBusy(false);
    }
  };

  const disconnectGithub = async () => {
    setGhBusy(true);
    try {
      await saveGithub({ token: "", name: "", email: "", login: "" });
      await refreshMcp();
      toast.success("GitHub déconnecté.");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Échec.");
    } finally {
      setGhBusy(false);
    }
  };

  const fetchedModels = models?.models || [];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[88vh] max-w-2xl flex-col gap-4 overflow-hidden">
        <DialogHeader>
          <DialogTitle>Réglages</DialogTitle>
          <DialogDescription>
            Connectez NEMESIS à un serveur NEMAPI local ou hébergé, puis choisissez le modèle.
          </DialogDescription>
        </DialogHeader>

        <Tabs defaultValue="connection" className="flex min-h-0 flex-1 flex-col">
          <TabsList className="no-scrollbar flex w-full shrink-0 items-center justify-start gap-1 overflow-x-auto sm:grid sm:grid-cols-8">
            <TabsTrigger value="connection" className="shrink-0">
              <Server className="mr-1.5 h-3.5 w-3.5" /> Connexion
            </TabsTrigger>
            <TabsTrigger value="general" className="shrink-0">
              <SlidersHorizontal className="mr-1.5 h-3.5 w-3.5" /> Général
            </TabsTrigger>
            <TabsTrigger value="memory" className="shrink-0">
              <Brain className="mr-1.5 h-3.5 w-3.5" /> Mémoire ({memoryBlocks.length})
            </TabsTrigger>
            <TabsTrigger value="mcp" className="shrink-0">
              <Plug className="mr-1.5 h-3.5 w-3.5" /> MCP ({mcpServers.length})
            </TabsTrigger>
            <TabsTrigger value="skills" className="shrink-0">
              <Package className="mr-1.5 h-3.5 w-3.5" /> Skills ({skills.length})
            </TabsTrigger>
            <TabsTrigger value="tools" className="shrink-0">
              <Workflow className="mr-1.5 h-3.5 w-3.5" /> Outils ({tools.length})
            </TabsTrigger>
            <TabsTrigger value="github" className="shrink-0">
              <Github className="mr-1.5 h-3.5 w-3.5" /> GitHub
            </TabsTrigger>
            <TabsTrigger value="telegram" className="shrink-0">
              <Send className="mr-1.5 h-3.5 w-3.5" /> Telegram
            </TabsTrigger>
          </TabsList>

          <div className="scrollbar-thin -mr-2 min-h-0 flex-1 overflow-y-auto pr-2">
            <TabsContent value="connection" className="space-y-4 pt-4">
            {/* Mode selector */}
            <div className="grid grid-cols-2 gap-2">
              <button
                type="button"
                onClick={() => setMode("local")}
                className={cn(
                  "flex items-start gap-2.5 rounded-lg border p-3 text-left transition-colors",
                  mode === "local"
                    ? "border-primary bg-primary/5"
                    : "border-border hover:border-primary/40",
                )}
              >
                <Server className="mt-0.5 h-4 w-4 text-primary" />
                <div>
                  <div className="text-sm font-medium">Serveur local</div>
                  <div className="text-[11px] text-muted-foreground">Adresse IP et port</div>
                </div>
                {mode === "local" && <Check className="ml-auto h-4 w-4 text-primary" />}
              </button>
              <button
                type="button"
                onClick={() => setMode("url")}
                className={cn(
                  "flex items-start gap-2.5 rounded-lg border p-3 text-left transition-colors",
                  mode === "url"
                    ? "border-primary bg-primary/5"
                    : "border-border hover:border-primary/40",
                )}
              >
                <Cloud className="mt-0.5 h-4 w-4 text-primary" />
                <div>
                  <div className="text-sm font-medium">URL publique</div>
                  <div className="text-[11px] text-muted-foreground">Serveur hébergé / tunnel</div>
                </div>
                {mode === "url" && <Check className="ml-auto h-4 w-4 text-primary" />}
              </button>
            </div>

            {mode === "local" ? (
              <div className="grid grid-cols-3 gap-3">
                <div className="col-span-2">
                  <Field label="Hôte / IP">
                    <Input
                      value={host}
                      onChange={(e) => setHost(e.target.value)}
                      placeholder="127.0.0.1"
                    />
                  </Field>
                </div>
                <Field label="Port">
                  <Input value={port} onChange={(e) => setPort(e.target.value)} placeholder="8090" />
                </Field>
              </div>
            ) : (
              <Field
                label="URL du serveur NEMAPI"
                hint={
                  <>
                    Exemple : <span className="font-mono">https://xxxx.trycloudflare.com/v1</span>.
                    Le préfixe <span className="font-mono">/v1</span> est conservé.
                  </>
                }
              >
                <Input
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  placeholder="https://xxxx.trycloudflare.com/v1"
                  spellCheck={false}
                  autoComplete="off"
                />
              </Field>
            )}

            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" onClick={testConnection} disabled={testing}>
                {testing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                Tester & charger les modèles
              </Button>
              {models?.connected ? (
                <Badge variant="success" className="gap-1">
                  <Check className="h-3 w-3" /> En ligne
                </Badge>
              ) : (
                <Badge variant="warning" className="gap-1">
                  <TriangleAlert className="h-3 w-3" /> Hors ligne
                </Badge>
              )}
            </div>

            <Field
              label="Modèle"
              hint={
                fetchedModels.length
                  ? "Sélectionnez un modèle détecté, ou saisissez un identifiant manuellement."
                  : "Aucun modèle détecté — saisissez directement l'identifiant du modèle (il sera enregistré)."
              }
            >
              <Input
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder="qwen-chat"
                list="model-options"
                spellCheck={false}
              />
              <datalist id="model-options">
                {fetchedModels.map((m) => (
                  <option key={m.id} value={m.id} />
                ))}
              </datalist>
            </Field>

            {fetchedModels.length > 0 && (
              <div className="space-y-1.5">
                <div className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                  Modèles disponibles ({fetchedModels.length})
                </div>
                <div className="grid max-h-48 grid-cols-1 gap-1.5 overflow-y-auto rounded-lg border border-border p-2 sm:grid-cols-2">
                  {fetchedModels.map((m) => (
                    <button
                      key={m.id}
                      type="button"
                      onClick={() => setModel(m.id)}
                      className={cn(
                        "flex flex-col rounded-md border px-2.5 py-1.5 text-left transition-colors",
                        model === m.id
                          ? "border-primary bg-primary/10"
                          : "border-border hover:border-primary/40 hover:bg-secondary/50",
                      )}
                    >
                      <span className="flex items-center gap-1.5">
                        <span className="truncate font-mono text-[11px] font-medium text-foreground">
                          {m.id}
                        </span>
                        {model === m.id && <Check className="ml-auto h-3 w-3 shrink-0 text-primary" />}
                      </span>
                      {m.display_name && m.display_name !== m.id && (
                        <span className="truncate text-[10px] text-muted-foreground">
                          {m.display_name}
                        </span>
                      )}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {models && !models.connected && models.error && (
              <p className="rounded-md border border-amber-500/40 bg-amber-500/5 px-2.5 py-2 font-mono text-[11px] text-amber-600 dark:text-amber-400">
                {models.error}
              </p>
            )}

            <Field label="Provider">
              <Input value={config?.provider?.type || "nemapi"} readOnly className="opacity-70" />
            </Field>
          </TabsContent>

          <TabsContent value="general" className="space-y-4">
            <Field label="Workspace" hint="Dossier de travail de l'agent (fichiers et commandes).">
              <div className="flex gap-2">
                <Input value={workspace} onChange={(e) => setWorkspace(e.target.value)} />
                <Select value="" onValueChange={(value) => value && setWorkspace(value)}>
                  <SelectTrigger className="w-11 px-2">
                    <FolderTree className="h-4 w-4" />
                  </SelectTrigger>
                  <SelectContent>
                    {QUICK_WORKSPACES.map((path) => (
                      <SelectItem key={path} value={path}>
                        {path}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </Field>

            <div className="space-y-1 rounded-lg border border-border p-3">
              <div className="flex items-center justify-between gap-4 py-1.5">
                <div>
                  <div className="text-sm font-medium">Auto-approuver les lectures</div>
                  <div className="text-[11px] text-muted-foreground">
                    Les outils en lecture seule (read, grep, glob…) s'exécutent sans confirmation.
                  </div>
                </div>
                <Switch checked={autoApproveReads} onCheckedChange={setAutoApproveReads} />
              </div>
              <div className="flex items-center justify-between gap-4 py-1.5">
                <div>
                  <div className="text-sm font-medium">Confirmer les actions à risque élevé</div>
                  <div className="text-[11px] text-muted-foreground">
                    Suppression, kill, MCP : demande toujours une autorisation explicite.
                  </div>
                </div>
                <Switch checked={confirmHighRisk} onCheckedChange={setConfirmHighRisk} />
              </div>
              <div className="flex items-center justify-between gap-4 py-1.5">
                <div>
                  <div className="text-sm font-medium">Afficher les sorties d'outils par défaut</div>
                  <div className="text-[11px] text-muted-foreground">
                    Déplie automatiquement les sorties dans le fil de discussion.
                  </div>
                </div>
<Switch checked={showOutputs} onCheckedChange={setShowOutputs} />
              </div>
            </div>
          </TabsContent>

          <TabsContent value="mcp" className="space-y-4 pt-4">
            <Field
              label="Ajouter un serveur MCP"
              hint="Commande de lancement du serveur (stdio). Les variables d'environnement peuvent être définies par la suite."
            >
              <div className="space-y-2">
                <div className="grid grid-cols-2 gap-2">
                  <Input
                    value={mcpName}
                    onChange={(e) => setMcpName(e.target.value)}
                    placeholder="nom (ex: github)"
                  />
                  <Input
                    value={mcpCommand}
                    onChange={(e) => setMcpCommand(e.target.value)}
                    placeholder="commande (ex: npx -y ...server-github)"
                  />
                </div>
                <Input
                  value={mcpArgs}
                  onChange={(e) => setMcpArgs(e.target.value)}
                  placeholder="arguments (optionnel, séparés par des espaces)"
                />
                <Input
                  value={mcpDesc}
                  onChange={(e) => setMcpDesc(e.target.value)}
                  placeholder="description (optionnel)"
                />
                <Button size="sm" onClick={addMcp} disabled={mcpBusy}>
                  {mcpBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="h-3.5 w-3.5" />}
                  Ajouter le serveur
                </Button>
              </div>
            </Field>

            <div className="space-y-1.5">
              <div className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                Serveurs configurés ({mcpServers.length})
              </div>
              <div className="divide-y divide-border rounded-lg border border-border">
                {mcpServers.map((server) => (
                  <div key={server.name} className="flex items-start gap-3 px-3 py-2.5">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-xs font-semibold">{server.name}</span>
                        {server.env.length > 0 && (
                          <Badge variant="secondary">{server.env.length} env</Badge>
                        )}
                      </div>
                      <p className="mt-0.5 break-all font-mono text-[11px] text-muted-foreground">
                        {server.command} {server.args.join(" ")}
                      </p>
                      {server.description && (
                        <p className="mt-0.5 text-[11px] text-muted-foreground">{server.description}</p>
                      )}
                    </div>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      onClick={() => void removeMcp(server.name)}
                      title="Supprimer"
                    >
                      <Trash2 className="h-3.5 w-3.5 text-destructive" />
                    </Button>
                  </div>
                ))}
                {mcpServers.length === 0 && (
                  <p className="px-3 py-6 text-center text-xs text-muted-foreground">
                    Aucun serveur MCP configuré.
                  </p>
                )}
              </div>
            </div>
          </TabsContent>

          <TabsContent value="skills" className="space-y-3 pt-4">
            <p className="text-[11px] text-muted-foreground">
              Compétences installées dans <span className="font-mono">tools_library/</span>. Elles étendent les
              capacités de l'agent via des fichiers <span className="font-mono">SKILL.md</span>.
            </p>
            <div className="divide-y divide-border rounded-lg border border-border">
              {skills.map((skill) => (
                <div key={skill.name} className="flex items-start gap-3 px-3 py-2.5">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-xs font-semibold">{skill.name}</span>
                      {skill.version && <Badge variant="secondary">v{skill.version}</Badge>}
                      {!skill.has_skill_md && <Badge variant="warning">sans SKILL.md</Badge>}
                    </div>
                    {skill.description && (
                      <p className="mt-0.5 line-clamp-2 text-[11px] text-muted-foreground">{skill.description}</p>
                    )}
                  </div>
                </div>
              ))}
              {skills.length === 0 && (
                <p className="px-3 py-6 text-center text-xs text-muted-foreground">
                  Aucune compétence installée.
                </p>
              )}
            </div>
          </TabsContent>

          <TabsContent value="memory" className="space-y-3 pt-4">
            <div className="flex items-start justify-between gap-4 rounded-lg border border-border p-3">
              <div className="min-w-0">
                <div className="text-sm font-medium">Utiliser la mémoire</div>
                <p className="mt-0.5 text-[11px] text-muted-foreground">
                  Quand activée, les informations mémorisées sont envoyées à l'agent au début de chaque nouvelle
                  session (web et Telegram partagent la même mémoire).
                </p>
              </div>
              <Switch checked={memoryEnabled} onCheckedChange={changeMemoryToggle} />
            </div>

            <div className="rounded-lg border border-border p-3">
              <div className="mb-2 text-xs font-medium text-muted-foreground">
                {memEditId ? "Modifier le bloc" : "Ajouter un bloc"}
              </div>
              <div className="space-y-2">
                <Input
                  value={memTitle}
                  onChange={(e) => setMemTitle(e.target.value)}
                  placeholder="Titre (ex: Préférences de langue)"
                />
                <textarea
                  value={memContent}
                  onChange={(e) => setMemContent(e.target.value)}
                  placeholder="Information à mémoriser (auto-suffisante)"
                  rows={3}
                  className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-1 focus:ring-ring"
                />
                <Input
                  value={memTags}
                  onChange={(e) => setMemTags(e.target.value)}
                  placeholder="Tags séparés par des virgules (optionnel)"
                />
                <div className="flex items-center gap-2">
                  <Button size="sm" onClick={saveMemoryBlock} disabled={memBusy}>
                    {memBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="h-3.5 w-3.5" />}
                    {memEditId ? "Mettre à jour" : "Ajouter"}
                  </Button>
                  {memEditId && (
                    <Button size="sm" variant="outline" onClick={resetMemForm}>
                      Annuler
                    </Button>
                  )}
                  <Button
                    size="sm"
                    variant="destructive"
                    className="ml-auto"
                    onClick={clearMemoryAll}
                    disabled={memoryBlocks.length === 0}
                  >
                    <Trash2 className="h-3.5 w-3.5" /> Tout supprimer
                  </Button>
                </div>
              </div>
            </div>

            <div className="divide-y divide-border rounded-lg border border-border">
              {memoryBlocks.map((block) => (
                <div key={block.id} className="flex items-start gap-3 px-3 py-2.5">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-semibold">{block.title || block.key || "Sans titre"}</span>
                      {(block.tags || []).map((tag) => (
                        <Badge key={tag} variant="secondary">
                          {tag}
                        </Badge>
                      ))}
                    </div>
                    <p className="mt-0.5 whitespace-pre-wrap break-words text-[11px] text-muted-foreground">
                      {block.content}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <Button size="icon" variant="ghost" onClick={() => editMemoryBlock(block)}>
                      <SlidersHorizontal className="h-3.5 w-3.5" />
                    </Button>
                    <Button size="icon" variant="ghost" onClick={() => removeMemoryBlock(block.id)}>
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </div>
              ))}
              {memoryBlocks.length === 0 && (
                <p className="px-3 py-6 text-center text-xs text-muted-foreground">
                  Aucune information en mémoire.
                </p>
              )}
            </div>
          </TabsContent>

          <TabsContent value="tools" className="space-y-3 pt-4">
            <Field label="Rechercher un outil">
              <Input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="bash, read_file, grep..."
              />
            </Field>
            <ScrollArea className="h-64 rounded-lg border border-border">
              <div className="divide-y divide-border">
                {filteredTools.map((tool) => (
                  <div key={tool.name} className="flex items-start gap-3 px-3 py-2.5">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-xs font-semibold">{tool.name}</span>
                        <Badge
                          variant={
                            tool.risk === "high"
                              ? "destructive"
                              : tool.risk === "medium"
                                ? "warning"
                                : "secondary"
                          }
                        >
                          {tool.risk}
                        </Badge>
                        {tool.kind && (
                          <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
                            {tool.kind}
                          </span>
                        )}
                      </div>
                      <p className="mt-0.5 line-clamp-2 text-[11px] text-muted-foreground">
                        {tool.description}
                      </p>
                    </div>
                  </div>
                ))}
                {filteredTools.length === 0 && (
                  <p className="px-3 py-6 text-center text-xs text-muted-foreground">
                    Aucun outil ne correspond.
                  </p>
                )}
              </div>
            </ScrollArea>
          </TabsContent>

          <TabsContent value="github" className="space-y-3 pt-4">
            <div className="flex items-start justify-between gap-4 rounded-lg border border-border p-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <Github className="h-4 w-4" />
                  <span className="text-sm font-medium">Connexion GitHub</span>
                  {github?.connected ? (
                    <Badge variant="success" className="gap-1">
                      <Check className="h-3 w-3" /> Connecté
                    </Badge>
                  ) : (
                    <Badge variant="secondary">Non connecté</Badge>
                  )}
                </div>
                <p className="mt-1 text-[11px] text-muted-foreground">
                  Enregistre un token personnel (PAT) et une identité Git. L'agent reçoit alors des
                  outils GitHub dédiés (dépôts, issues, pull requests, recherche de code) via un serveur
                  MCP, sans alourdir le registre d'outils principal.
                </p>
              </div>
            </div>

            <div className="rounded-lg border border-border p-3">
              <div className="space-y-3">
                <Field
                  label="Token personnel GitHub (PAT)"
                  hint="Scopes recommandés : repo, read:org, workflow. Stocké localement dans le répertoire de config partagé."
                >
                  <Input
                    value={ghToken}
                    onChange={(e) => setGhToken(e.target.value)}
                    type="password"
                    autoComplete="off"
                    spellCheck={false}
                    placeholder={
                      github?.token_set ? `Configuré (${github.token_preview})` : "ghp_xxx ou github_pat_xxx"
                    }
                  />
                </Field>

                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <Field label="Nom Git (user.name)">
                    <Input
                      value={ghName}
                      onChange={(e) => setGhName(e.target.value)}
                      placeholder="Octocat"
                    />
                  </Field>
                  <Field label="Email Git (user.email)">
                    <Input
                      value={ghEmail}
                      onChange={(e) => setGhEmail(e.target.value)}
                      type="email"
                      placeholder="octocat@example.com"
                    />
                  </Field>
                </div>

                <Field label="Identifiant GitHub (optionnel)">
                  <Input
                    value={ghLogin}
                    onChange={(e) => setGhLogin(e.target.value)}
                    placeholder="octocat"
                  />
                </Field>

                <div className="flex flex-wrap items-center gap-2">
                  <Button size="sm" onClick={saveGithubConfig} disabled={ghBusy}>
                    {ghBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
                    Enregistrer
                  </Button>
                  {github?.connected && (
                    <Button size="sm" variant="outline" onClick={disconnectGithub} disabled={ghBusy}>
                      <Trash2 className="h-3.5 w-3.5" /> Déconnecter
                    </Button>
                  )}
                </div>
              </div>
            </div>

            <div className="rounded-lg border border-border p-3">
              <div className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                Serveur MCP GitHub
              </div>
              <p className="mt-1 break-all font-mono text-[11px] text-muted-foreground">
                {github?.mcp_command || "npx -y @modelcontextprotocol/server-github"}
              </p>
              <p className="mt-1.5 text-[11px] text-muted-foreground">
                Nécessite Node.js / <span className="font-mono">npx</span> sur la machine. Une fois connecté,
                l'agent peut appeler <span className="font-mono">mcp_tools_list("github")</span> puis{" "}
                <span className="font-mono">mcp_call</span>.
              </p>
            </div>
          </TabsContent>

          <TabsContent value="telegram" className="space-y-3 pt-4">
            <p className="text-[11px] text-muted-foreground">
              Le bot Telegram et l'UI web partagent la même mémoire, les mêmes sessions et utilisent le même
              moteur NEMESIS. Le bot dispose de toutes les autorisations d'exécution par défaut.
            </p>

            <div className="rounded-lg border border-border p-3">
              <Field
                label="Token du bot Telegram"
                hint="Obtenez-le via @BotFather. Stocké dans le répertoire de configuration partagé."
              >
                <Input
                  value={tgToken}
                  onChange={(e) => setTgToken(e.target.value)}
                  type="password"
                  placeholder={telegram?.token_set ? `Configuré (${telegram.token_preview})` : "123456:ABC-DEF..."}
                />
              </Field>
              <div className="mt-3">
                <Field label="Nom du bot (optionnel)">
                  <Input
                    value={tgBotName}
                    onChange={(e) => setTgBotName(e.target.value)}
                    placeholder={telegram?.bot_name || "@mon_bot"}
                  />
                </Field>
              </div>
              <div className="mt-3 flex items-center gap-2">
                <Button size="sm" onClick={saveTelegramConfig} disabled={tgBusy}>
                  {tgBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
                  Connecter le bot
                </Button>
                {telegram?.token_set && <Badge variant="secondary">Token configuré</Badge>}
              </div>
            </div>

            <div className="grid grid-cols-2 gap-2">
              <div className="rounded-lg border border-border p-3">
                <div className="text-[11px] font-medium text-muted-foreground">Mémoire partagée</div>
                <div className="mt-1 text-sm">{telegram?.memory_shared ? "Activée" : "—"}</div>
              </div>
              <div className="rounded-lg border border-border p-3">
                <div className="text-[11px] font-medium text-muted-foreground">Autorisations</div>
                <div className="mt-1 text-sm">Toutes (par défaut)</div>
              </div>
            </div>

            <p className="text-[11px] text-muted-foreground">
              Pour lancer le bot, exécutez <span className="font-mono">python telegram_bot.py</span> ou{" "}
              <span className="font-mono">./run_telegram_bot.sh</span>. Le token enregistré ici sera utilisé
              automatiquement.
            </p>
          </TabsContent>
          </div>
        </Tabs>

        <DialogFooter className="shrink-0">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Annuler
          </Button>
          <Button onClick={save} disabled={saving}>
            <Save className="h-4 w-4" />
            {saving ? "Enregistrement…" : "Enregistrer"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}