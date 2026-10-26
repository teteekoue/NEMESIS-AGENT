import { create } from "zustand";
import {
  api,
  type FileContent,
  type FileListing,
  type GithubConfig,
  type McpServer,
  type MemoryBlock,
  type ModelsResponse,
  type PublicConfig,
  type Risk,
  type SessionDetail,
  type SessionMeta,
  type SkillInfo,
  type StoredMessage,
  type TelegramConfig,
  type ToolInfo,
} from "@/lib/api";
import { buildDemoSession } from "@/lib/demoSession";

export type ApprovalMode = "all" | "confirm";

export interface ChatMessage {
  id: string;
  kind: "user" | "assistant" | "tool" | "notice" | "error";
  text?: string;
  name?: string;
  params?: Record<string, unknown>;
  risk?: Risk;
  status?: "pending" | "running" | "done" | "denied";
  success?: boolean;
  output?: string;
  exitCode?: number;
  at: number;
  startedAt?: number;
  duration?: number;
  turnElapsed?: number;
  toolCount?: number;
  images?: string[];
  imageCount?: number;
}

export interface AuthRequest {
  id: string;
  name: string;
  params: Record<string, unknown>;
  risk: Risk;
}

export interface ProviderInfo {
  type: string;
  model: string;
  host: string | number;
  port: string | number;
  approval_mode?: ApprovalMode;
}

interface AppState {
  connected: boolean;
  relayConnected: boolean;
  backendOffline: boolean;
  demo: boolean;
  agentReachable: boolean | null;
  provider: ProviderInfo | null;
  messages: ChatMessage[];
  busy: boolean;
  statusText: string;
  turnStartedAt: number | null;
  lastTurnElapsed: number | null;
  pendingAuth: AuthRequest | null;
  sessions: SessionMeta[];
  sessionId: string | null;
  config: PublicConfig | null;
  models: ModelsResponse | null;
  tools: ToolInfo[];
  mcpServers: McpServer[];
  skills: SkillInfo[];
  memoryBlocks: MemoryBlock[];
  memoryEnabled: boolean;
  telegram: TelegramConfig | null;
  github: GithubConfig | null;
  approvalMode: ApprovalMode;
  files: FileListing | null;
  filePath: string;
  openFile: FileContent | null;
  logs: string[];
  theme: "dark" | "light";

  bootstrap: () => Promise<void>;
  retryBootstrap: () => Promise<void>;
  connectSocket: () => void;
  connectRelay: () => void;
  reconnectAgent: () => void;
  sendMessage: (text: string, images?: string[]) => Promise<void>;
  authorize: (choice: "y" | "n" | "a") => void;
  interrupt: () => void;
  selectModel: (model: string) => Promise<void>;
  newSession: () => Promise<void>;
  openSession: (id: string) => Promise<void>;
  removeSession: (id: string) => Promise<void>;
  refreshSessions: () => Promise<void>;
  refreshConfig: () => Promise<void>;
  saveConfig: (patch: Partial<PublicConfig>) => Promise<boolean>;
  refreshModels: () => Promise<void>;
  refreshTools: () => Promise<void>;
  refreshMcp: () => Promise<void>;
  refreshSkills: () => Promise<void>;
  refreshMemory: () => Promise<void>;
  upsertMemory: (payload: { id?: string; key?: string; title?: string; content?: string; tags?: string[] }) => Promise<void>;
  deleteMemory: (id: string) => Promise<void>;
  clearMemory: () => Promise<void>;
  toggleMemory: (enabled: boolean) => Promise<void>;
  refreshTelegram: () => Promise<void>;
  saveTelegram: (payload: {
    token?: string;
    bot_name?: string;
    workspace?: string;
    nemapi?: { host?: string; port?: number; model?: string };
  }) => Promise<void>;
  refreshGithub: () => Promise<void>;
  saveGithub: (payload: {
    token?: string;
    name?: string;
    email?: string;
    login?: string;
  }) => Promise<void>;
  setApprovalMode: (mode: ApprovalMode, persist?: boolean) => Promise<void>;
  refreshFiles: (path?: string) => Promise<void>;
  openWorkspaceFile: (path: string) => Promise<void>;
  closeFile: () => void;
  clearLogs: () => void;
  toggleTheme: () => void;
}

let socket: WebSocket | null = null;
let relaySocket: WebSocket | null = null;
let relayRetryTimer: ReturnType<typeof setTimeout> | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let manualClose = false;
let pendingSend: string | null = null;
let msgSeq = 0;

const ACTIVE_SESSION_KEY = "nemesis-active-session";

function rememberActiveSession(id: string | null) {
  try {
    if (id) localStorage.setItem(ACTIVE_SESSION_KEY, id);
    else localStorage.removeItem(ACTIVE_SESSION_KEY);
  } catch {
    /* ignore */
  }
}

function readActiveSession(): string | null {
  try {
    return localStorage.getItem(ACTIVE_SESSION_KEY);
  } catch {
    return null;
  }
}

const uid = () => `m${Date.now().toString(36)}${(msgSeq++).toString(36)}`;
const now = () => Date.now() / 1000;

function toStored(message: ChatMessage): StoredMessage {
  const { id, kind, images, ...rest } = message;
  return { kind, ...rest } as StoredMessage;
}

function logLine(stamp: string, text: string): string {
  return `[${stamp}] ${text}`;
}

async function runRelayRequest(payload: any) {
  const id = String(payload.id || "");
  const reply = (data: Record<string, unknown>) => {
    if (relaySocket?.readyState === WebSocket.OPEN) {
      relaySocket.send(JSON.stringify({ type: "relay_response", id, ...data }));
    }
  };
  try {
    const response = await fetch(String(payload.url), {
      method: String(payload.method || "GET"),
      headers: (payload.headers as Record<string, string>) || {},
      body: payload.body ?? undefined,
    });
    const body = await response.text();
    reply({ status: response.status, body });
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    reply({
      error: `${detail} — vérifie que l'URL NemApi est joignable depuis ce navigateur et que CORS est autorisé.`,
    });
  }
}

export const useAppStore = create<AppState>((set, get) => ({
  connected: false,
  relayConnected: false,
  backendOffline: false,
  demo: false,
  agentReachable: null,
  provider: null,
  messages: [],
  busy: false,
  statusText: "",
  turnStartedAt: null,
  lastTurnElapsed: null,
  pendingAuth: null,
  sessions: [],
  sessionId: null,
  config: null,
  models: null,
  tools: [],
  mcpServers: [],
  skills: [],
  memoryBlocks: [],
  memoryEnabled: true,
  telegram: null,
  github: null,
  approvalMode: "all",
  files: null,
  filePath: "",
  openFile: null,
  logs: [],
  theme:
    (typeof localStorage !== "undefined" &&
      (localStorage.getItem("nemesis-theme") as "dark" | "light")) ||
    "light",

  bootstrap: async () => {
    const params = new URLSearchParams(window.location.search);

    if (params.get("demo") === "1") {
      // Offline demo mode: render a fictional, fully-populated session.
      // No socket is opened so the simulated conversation stays static.
      manualClose = true;
      set({ messages: buildDemoSession(), connected: true, backendOffline: false, demo: true });
      const config = await api.getConfig().catch(() => null);
      if (config) set({ config });
      await Promise.all([get().refreshTools(), get().refreshFiles()]);
      void get().refreshModels();
      return;
    }
    manualClose = false;
    get().connectRelay();

    // Reopen the last active conversation so a page reload (or a backend
    // restart) resumes where the user left off instead of showing nothing.
    // openSession() already (re)connects the socket for that session.
    const restoreId = readActiveSession();
    if (restoreId) {
      await get().openSession(restoreId);
    } else {
      get().connectSocket();
    }

    const [config] = await Promise.all([
      api.getConfig().catch(() => null),
      get().refreshSessions(),
      get().refreshTools(),
      get().refreshFiles(),
    ]);
    if (config) {
      set({ config, approvalMode: config.security?.approval_mode === "all" ? "all" : "confirm" });
    }
    void get().refreshMcp();
    void get().refreshSkills();
    void get().refreshMemory();
    void get().refreshGithub();
    void get().refreshTelegram();

    let reachable = false;
    try {
      await api.health();
      reachable = true;
    } catch {
      reachable = false;
    }

    // The mock session is only shown when explicitly requested with ?demo=1.
    // Without a reachable backend we surface a clear offline state instead of
    // silently rendering fictional content.
    set({ backendOffline: !reachable });

    void get().refreshModels();
  },

  retryBootstrap: async () => {
    set({ backendOffline: false });
    await get().bootstrap();
  },

  connectSocket: () => {
    manualClose = false;
    const { sessionId, config, approvalMode } = get();
    const model = config?.model || "";
    const params = new URLSearchParams();
    if (sessionId) params.set("session", sessionId);
    if (model) params.set("model", model);
    params.set("mode", approvalMode);
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const url = `${proto}://${window.location.host}/ws/chat?${params.toString()}`;

    if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
      return;
    }

    const ws = new WebSocket(url);
    socket = ws;

    ws.onopen = () => {
      set({ connected: true, backendOffline: false });
      if (pendingSend) {
        const payload = pendingSend;
        pendingSend = null;
        try {
          ws.send(payload);
        } catch {
          /* ignore */
        }
      }
    };

    ws.onclose = () => {
      set({ connected: false, busy: false, pendingAuth: null });
      if (!manualClose) {
        if (reconnectTimer) clearTimeout(reconnectTimer);
        reconnectTimer = setTimeout(() => get().connectSocket(), 2500);
      }
    };

    ws.onerror = () => set({ connected: false });

    ws.onmessage = (event) => {
      let payload: any;
      try {
        payload = JSON.parse(event.data);
      } catch {
        return;
      }
      handleEvent(payload, set, get);
    };
  },

  reconnectAgent: () => {
    set({ agentReachable: null, busy: false, pendingAuth: null, statusText: "" });
    get().connectRelay();
    reconnect();
  },

  connectRelay: () => {
    if (
      relaySocket &&
      (relaySocket.readyState === WebSocket.OPEN || relaySocket.readyState === WebSocket.CONNECTING)
    ) {
      return;
    }
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${window.location.host}/ws/relay`);
    relaySocket = ws;

    ws.onopen = () => set({ relayConnected: true });
    ws.onmessage = (event) => {
      let payload: any;
      try {
        payload = JSON.parse(event.data);
      } catch {
        return;
      }
      if (payload?.type === "relay_request") void runRelayRequest(payload);
    };
    ws.onerror = () => set({ relayConnected: false });
    ws.onclose = () => {
      set({ relayConnected: false });
      if (relayRetryTimer !== null) clearTimeout(relayRetryTimer);
      relayRetryTimer = setTimeout(() => {
        relayRetryTimer = null;
        useAppStore.getState().connectRelay();
      }, 3000);
    };
  },

  sendMessage: async (text: string, images: string[] = []) => {
    const trimmed = text.trim();
    if (!trimmed && images.length === 0) return;
    let sid = get().sessionId;
    if (!sid) {
      try {
        const created = await api.createSession({ model: get().config?.model });
        sid = created.id;
        set({ sessionId: created.id });
        rememberActiveSession(created.id);
        await get().refreshSessions();
      } catch {
        /* ignore */
      }
    }

    const message: ChatMessage = {
      id: uid(),
      kind: "user",
      text: trimmed,
      at: now(),
      ...(images.length ? { images } : {}),
    };
    set((state) => ({ messages: [...state.messages, message], turnStartedAt: now(), lastTurnElapsed: null }));

    if (sid) {
      void api.appendMessage(sid, toStored(message)).catch(() => {});
      if (get().messages.filter((m) => m.kind === "user").length === 1) {
        void api
          .renameSession(sid, trimmed.slice(0, 48) || "Image")
          .then(() => get().refreshSessions())
          .catch(() => {});
      }
    }

const payload = JSON.stringify({
      type: "user_message",
      content: trimmed || "Décris et analyse cette image.",
      ...(images.length ? { images } : {}),
    });
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(payload);
    } else {
      // Socket still connecting/closed: wait for it (or reconnect) then send
      // instead of silently dropping the user's message.
      if (!socket || socket.readyState === WebSocket.CLOSED) {
        reconnect();
      }
      pendingSend = payload;
      set((s) => ({
        messages: [
          ...s.messages,
          { id: uid(), kind: "notice", text: "Connexion à l'agent en cours...", at: now() },
        ],
      }));
    }
  },

  authorize: (choice) => {
    const pending = get().pendingAuth;
    if (!pending || socket?.readyState !== WebSocket.OPEN) return;
    socket.send(JSON.stringify({ type: "authorize", id: pending.id, choice }));
    set((state) => ({
      pendingAuth: null,
      messages: state.messages.map((m) =>
        m.kind === "tool" && m.status === "pending" && m.name === pending.name
          ? { ...m, status: choice === "n" ? "denied" : "running" }
          : m,
      ),
    }));
  },

  interrupt: () => {
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "interrupt" }));
    }
  },

  selectModel: async (model: string) => {
    await get().saveConfig({ provider: { model } });
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "set_model", model }));
    }
    const config = get().config;
    if (config) {
      set({ config: { ...config, model, provider: { ...config.provider, model } } });
    }
  },

  newSession: async () => {
    const created = await api.createSession({ model: get().config?.model });
    set({
      sessionId: created.id,
      messages: [],
      logs: [],
      pendingAuth: null,
      busy: false,
      statusText: "",
      turnStartedAt: null,
    });
    rememberActiveSession(created.id);
    await get().refreshSessions();
    reconnect();
  },

  openSession: async (id: string) => {
    try {
      const detail: SessionDetail = await api.getSession(id);
      const messages: ChatMessage[] = (detail.messages || []).map((m, index) => ({
        ...m,
        id: `s${index}-${Date.now().toString(36)}`,
        at: m.at || detail.updated_at,
      })) as ChatMessage[];
      set({ sessionId: id, messages, logs: [], pendingAuth: null, busy: false, statusText: "" });
      rememberActiveSession(id);
      reconnect();
    } catch (error) {
      rememberActiveSession(null);
      set((state) => ({
        sessionId: null,
        messages: [
          ...state.messages,
          {
            id: uid(),
            kind: "error",
            text: `Impossible d'ouvrir la conversation : ${(error as Error).message}`,
            at: now(),
          },
        ],
        pendingAuth: null,
        busy: false,
      }));
    }
  },

  removeSession: async (id: string) => {
    await api.deleteSession(id).catch(() => {});
    const isCurrent = get().sessionId === id;
    await get().refreshSessions();
    if (isCurrent) {
      rememberActiveSession(null);
      set({ sessionId: null, messages: [], logs: [] });
      reconnect();
    }
  },

  refreshSessions: async () => {
    try {
      const { sessions } = await api.listSessions();
      set({ sessions });
    } catch {
      /* ignore */
    }
  },

  refreshConfig: async () => {
    try {
      const config = await api.getConfig();
      set({ config });
    } catch {
      /* ignore */
    }
  },

  saveConfig: async (patch) => {
    try {
      const config = await api.patchConfig(patch);
      set((state) => ({
        config: state.config
          ? {
              ...state.config,
              ...config,
              provider: { ...state.config.provider, ...(config.provider || {}) },
              nemapi: { ...state.config.nemapi, ...(config.nemapi || {}) },
              security: { ...state.config.security, ...(config.security || {}) },
            }
          : config,
        approvalMode: config.security?.approval_mode === "all" ? "all" : state.approvalMode,
        agentReachable: null,
      }));
      return true;
    } catch {
      return false;
    }
  },

  refreshModels: async () => {
    try {
      const models = await api.getModels();
      set({ models, agentReachable: models.connected });
    } catch {
      set({ agentReachable: false });
    }
  },

  refreshTools: async () => {
    try {
      const { tools } = await api.getTools();
      set({ tools });
    } catch {
      /* ignore */
    }
  },

  refreshMcp: async () => {
    try {
      const { servers } = await api.getMcpServers();
      set({ mcpServers: servers });
    } catch {
      /* ignore */
    }
  },

  refreshSkills: async () => {
    try {
      const { skills } = await api.getSkills();
      set({ skills });
    } catch {
      /* ignore */
    }
  },

  refreshMemory: async () => {
    try {
      const state = await api.getMemory();
      set({ memoryBlocks: state.blocks, memoryEnabled: state.enabled });
    } catch {
      /* ignore */
    }
  },

  upsertMemory: async (payload) => {
    const state = await api.upsertMemory(payload);
    set({ memoryBlocks: state.blocks, memoryEnabled: state.enabled });
  },

  deleteMemory: async (id) => {
    const state = await api.deleteMemory(id);
    set({ memoryBlocks: state.blocks, memoryEnabled: state.enabled });
  },

  clearMemory: async () => {
    const state = await api.clearMemory();
    set({ memoryBlocks: state.blocks, memoryEnabled: state.enabled });
  },

  toggleMemory: async (enabled) => {
    const state = await api.toggleMemory(enabled);
    set({ memoryBlocks: state.blocks, memoryEnabled: state.enabled });
  },

  refreshTelegram: async () => {
    try {
      const cfg = await api.getTelegram();
      set({ telegram: cfg });
    } catch {
      /* ignore */
    }
  },

  saveTelegram: async (payload) => {
    const cfg = await api.saveTelegram(payload);
    set({ telegram: cfg });
  },

  refreshGithub: async () => {
    try {
      const cfg = await api.getGithub();
      set({ github: cfg });
    } catch {
      /* ignore */
    }
  },

  saveGithub: async (payload) => {
    const cfg = await api.saveGithub(payload);
    set({ github: cfg });
  },

  setApprovalMode: async (mode, persist = true) => {
    set({ approvalMode: mode });
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "set_mode", mode }));
    }
    if (persist) {
      await get().saveConfig({ security: { approval_mode: mode } } as any);
    }
  },

  refreshFiles: async (path) => {
    const target = path ?? get().filePath ?? "";
    try {
      const files = await api.listFiles(target);
      set({ files, filePath: files.path });
    } catch {
      /* ignore */
    }
  },

  openWorkspaceFile: async (path) => {
    try {
      const content = await api.readFile(path);
      set({ openFile: content });
    } catch {
      /* ignore */
    }
  },

  closeFile: () => set({ openFile: null }),
  clearLogs: () => set({ logs: [] }),

  toggleTheme: () => {
    const next = get().theme === "dark" ? "light" : "dark";
    document.documentElement.classList.toggle("dark", next === "dark");
    try {
      localStorage.setItem("nemesis-theme", next);
    } catch {
      /* ignore */
    }
    set({ theme: next });
  },
}));

function reconnect() {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  if (socket) {
    manualClose = true;
    try {
      socket.onclose = null;
      socket.onerror = null;
      socket.onmessage = null;
    } catch {
      /* ignore */
    }
    socket.close();
    socket = null;
  }
  setTimeout(() => {
    manualClose = false;
    useAppStore.getState().connectSocket();
  }, 60);
}

function handleEvent(
  payload: any,
  set: (partial: Partial<AppState> | ((state: AppState) => Partial<AppState>)) => void,
  get: () => AppState,
) {
  const stamp = new Date().toLocaleTimeString("fr-FR", { hour12: false });
  const push = (message: ChatMessage) =>
    set((state) => ({ messages: [...state.messages, message] }));
  const persist = (message: ChatMessage) => {
    const sid = get().sessionId;
    if (sid) void api.appendMessage(sid, toStored(message)).catch(() => {});
  };

  switch (payload.type) {
    case "ready": {
      set({
        connected: true,
        provider: payload.provider ?? null,
        agentReachable: null,
        approvalMode: payload.provider?.approval_mode === "all" ? "all" : get().approvalMode,
      });
      void get().refreshModels();
      break;
    }
    case "mode_changed": {
      set({ approvalMode: payload.mode === "all" ? "all" : "confirm" });
      break;
    }
case "status": {
      set((state) => ({
        busy: true,
        statusText: payload.state === "thinking" ? "Réflexion..." : "",
        turnStartedAt: state.turnStartedAt ?? payload.at ?? now(),
      }));
      break;
    }
    case "assistant_text": {
      const text = String(payload.text || "").trim();
      if (!text) break;
      const state = get();
      const last = state.messages[state.messages.length - 1];
      if (last && last.kind === "assistant") {
        set({
          messages: state.messages.map((m) =>
            m.id === last.id ? { ...m, text: `${m.text}\n\n${text}` } : m,
          ),
        });
      } else {
        const message: ChatMessage = { id: uid(), kind: "assistant", text, at: now() };
        push(message);
        persist(message);
      }
      break;
    }
    case "tool_start": {
      const state = get();
      let pendingIndex = -1;
      for (let i = state.messages.length - 1; i >= 0; i -= 1) {
        const m = state.messages[i];
        if (m.kind === "tool" && m.status === "pending" && m.name === payload.name) {
          pendingIndex = i;
          break;
        }
      }
      const logs = [...state.logs, logLine(stamp, `→ ${payload.name}`)];
      if (pendingIndex >= 0) {
        const updated: ChatMessage = {
          ...state.messages[pendingIndex],
          status: "running",
          params: payload.params || state.messages[pendingIndex].params,
          risk: payload.risk ?? state.messages[pendingIndex].risk,
          startedAt: payload.at ?? now(),
        };
        const messages = state.messages.slice();
        messages[pendingIndex] = updated;
        set({ messages, logs });
        persist(updated);
      } else {
        const message: ChatMessage = {
          id: uid(),
          kind: "tool",
          name: payload.name,
          params: payload.params || {},
          risk: payload.risk,
          status: "running",
          startedAt: payload.at ?? now(),
          at: now(),
        };
        push(message);
        persist(message);
        set({ logs });
      }
      break;
    }
    case "tool_result": {
      const state = get();
      let target: ChatMessage | undefined;
      for (let i = state.messages.length - 1; i >= 0; i -= 1) {
        const m = state.messages[i];
        if (m.kind === "tool" && m.status === "running" && m.name === payload.name) {
          target = m;
          break;
        }
      }
      const success = Boolean(payload.success);
      set({
        messages: state.messages.map((m) =>
          target && m.id === target.id
            ? {
                ...m,
                status: "done",
                success,
                output: payload.output,
                exitCode: payload.exit_code,
                duration: payload.duration,
                imageCount: payload.image_count,
              }
            : m,
        ),
        logs: [
          ...state.logs,
          logLine(stamp, `${success ? "✓" : "✗"} ${payload.name}${payload.output ? ` — ${String(payload.output).slice(0, 160)}` : ""}`),
        ],
      });
      break;
    }
    case "tool_denied": {
      const state = get();
      let pendingIndex = -1;
      for (let i = state.messages.length - 1; i >= 0; i -= 1) {
        const m = state.messages[i];
        if (m.kind === "tool" && m.name === payload.name && (m.status === "pending" || m.status === "running")) {
          pendingIndex = i;
          break;
        }
      }
      const logs = [...state.logs, logLine(stamp, `⨯ ${payload.name} refusé`)];
      if (pendingIndex >= 0) {
        const updated: ChatMessage = { ...state.messages[pendingIndex], status: "denied" };
        const messages = state.messages.slice();
        messages[pendingIndex] = updated;
        set({ messages, logs });
      } else {
        const message: ChatMessage = {
          id: uid(),
          kind: "tool",
          name: payload.name,
          params: payload.params || {},
          risk: payload.risk,
          status: "denied",
          at: now(),
        };
        push(message);
        persist(message);
        set({ logs });
      }
      break;
    }
    case "authorize_request": {
      set({
        pendingAuth: {
          id: payload.id,
          name: payload.name,
          params: payload.params || {},
          risk: payload.risk,
        },
        messages: [
          ...get().messages,
          {
            id: uid(),
            kind: "tool",
            name: payload.name,
            params: payload.params || {},
            risk: payload.risk,
            status: "pending",
            at: now(),
          },
        ],
      });
      break;
    }
    case "notice": {
      const message: ChatMessage = { id: uid(), kind: "notice", text: payload.message, at: now() };
      push(message);
      persist(message);
      set((state) => ({ logs: [...state.logs, logLine(stamp, `ℹ ${payload.message}`)] }));
      break;
    }
    case "error": {
      const message: ChatMessage = { id: uid(), kind: "error", text: payload.message, at: now() };
      push(message);
      persist(message);
      set((state) => ({ logs: [...state.logs, logLine(stamp, `! ${payload.message}`)] }));
      break;
    }
    case "turn_end": {
      const elapsed = typeof payload.elapsed === "number" ? payload.elapsed : null;
      set((state) => {
        const messages = state.messages.slice();
        for (let i = messages.length - 1; i >= 0; i -= 1) {
          if (messages[i].kind === "assistant") {
            messages[i] = {
              ...messages[i],
              turnElapsed: elapsed ?? undefined,
              toolCount: typeof payload.tool_count === "number" ? payload.tool_count : undefined,
            };
            break;
          }
        }
        return {
          busy: false,
          statusText: "",
          pendingAuth: null,
          turnStartedAt: null,
          lastTurnElapsed: elapsed,
          messages,
        };
      });
      void get().refreshSessions();
      break;
    }
    default:
      break;
  }
}
