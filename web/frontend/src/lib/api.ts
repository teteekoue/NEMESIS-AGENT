export type Risk = "read" | "medium" | "high" | string;

export interface ToolInfo {
  name: string;
  description: string;
  risk: Risk;
  kind?: string;
}

export interface ModelInfo {
  id: string;
  owned_by?: string;
  display_name?: string;
}

export interface ModelsResponse {
  connected: boolean;
  models: ModelInfo[];
  current: string;
  error?: string;
}

export interface PublicConfig {
  provider: { type?: string; model?: string };
  nemapi: { host?: string; port?: number; url?: string };
  mode?: "local" | "url";
  workspace: string;
  model: string;
  security?: {
    workspace?: string;
    approval_mode?: "all" | "confirm";
    auto_approve_reads?: boolean;
    confirm_high_risk?: boolean;
    show_tool_outputs?: boolean;
  };
}

export interface SubagentProfile {
  id: string;
  name: string;
  provider: "groq" | "openai" | "custom";
  base_url: string;
  model: string;
  api_key_set: boolean;
  api_key_from_env: boolean;
  api_key_env_name: string;
}

export interface SubagentsConfig {
  profiles: SubagentProfile[];
  profile_count: number;
  max_concurrent: number;
  max_iterations: number;
  timeout_seconds: number;
  max_retries: number;
  max_tokens_per_task: number;
}

export interface McpServer {
  name: string;
  command: string;
  args: string[];
  description: string;
  env: string[];
}

export interface SkillInfo {
  name: string;
  description: string;
  version: string;
  has_skill_md: boolean;
  enabled: boolean;
  source: string;
}

export interface MemoryBlock {
  id: string;
  key: string;
  title: string;
  content: string;
  tags: string[];
  created_at: number;
  updated_at: number;
}

export interface MemoryState {
  enabled: boolean;
  blocks: MemoryBlock[];
}

export interface GithubConfig {
  connected: boolean;
  token_set: boolean;
  token_preview: string;
  name: string;
  email: string;
  login: string;
  mcp_server: string;
  mcp_command: string;
}

export interface TelegramConfig {
  token_set: boolean;
  token_preview: string;
  bot_name: string;
  workspace: string;
  nemapi: { host: string; port: number; model: string };
  memory_shared: boolean;
  approval_mode: string;
}

export interface SessionMeta {
  id: string;
  title: string;
  model: string;
  created_at: number;
  updated_at: number;
  message_count: number;
}

export interface StoredMessage {
  kind: "user" | "assistant" | "tool" | "notice" | "error";
  text?: string;
  name?: string;
  params?: Record<string, unknown>;
  risk?: Risk;
  status?: "pending" | "running" | "done" | "denied";
  success?: boolean;
  output?: string;
  at?: number;
}

export interface SessionDetail {
  id: string;
  title: string;
  model: string;
  messages: StoredMessage[];
  created_at: number;
  updated_at: number;
}

export interface FileEntry {
  name: string;
  path: string;
  is_dir: boolean;
  size: number;
  modified: number;
}

export interface FileListing {
  path: string;
  entries: FileEntry[];
}

export interface FileContent {
  path: string;
  content: string;
  truncated: boolean;
  binary: boolean;
  size: number;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export const api = {
  health: () => request<{ status: string }>("/api/health"),
  getConfig: () => request<PublicConfig>("/api/config"),
  patchConfig: (patch: Partial<PublicConfig>) =>
    request<PublicConfig>("/api/config", {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),
  getSubagentsConfig: () => request<SubagentsConfig>("/api/subagents"),
  saveSubagentProfile: (payload: {
    id?: string;
    name: string;
    provider: string;
    base_url: string;
    model: string;
    api_key?: string;
  }) =>
    request<SubagentsConfig>("/api/subagents/profiles", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  deleteSubagentProfile: (id: string) =>
    request<SubagentsConfig>(`/api/subagents/profiles/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),
  saveSubagentsConfig: (payload: {
    max_concurrent: number;
    max_iterations: number;
    timeout_seconds: number;
    max_retries: number;
    max_tokens_per_task: number;
  }) =>
    request<SubagentsConfig>("/api/subagents", {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  getModels: () => request<ModelsResponse>("/api/models"),
  chooseModel: (model: string) =>
    request<{ ok: boolean; model: string }>("/api/models", {
      method: "POST",
      body: JSON.stringify({ model }),
    }),
  getTools: () => request<{ tools: ToolInfo[] }>("/api/tools"),
  listFiles: (path = "") =>
    request<FileListing>(`/api/files?path=${encodeURIComponent(path)}`),
  readFile: (path: string) =>
    request<FileContent>(`/api/files/content?path=${encodeURIComponent(path)}`),
  listSessions: () => request<{ sessions: SessionMeta[] }>("/api/sessions"),
  createSession: (payload: { title?: string; model?: string } = {}) =>
    request<SessionDetail>("/api/sessions", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getSession: (id: string) => request<SessionDetail>(`/api/sessions/${id}`),
  renameSession: (id: string, title: string) =>
    request<SessionDetail>(`/api/sessions/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    }),
  appendMessage: (id: string, message: StoredMessage) =>
    request<{ ok: boolean }>(`/api/sessions/${id}/messages`, {
      method: "POST",
      body: JSON.stringify({ message }),
    }),
  deleteSession: (id: string) =>
    request<{ ok: boolean }>(`/api/sessions/${id}`, { method: "DELETE" }),
  getMcpServers: () => request<{ servers: McpServer[] }>("/api/mcp"),
  addMcpServer: (payload: {
    name: string;
    command: string;
    args?: string[];
    env?: Record<string, string>;
    description?: string;
  }) =>
    request<{ servers: McpServer[]; message?: string }>("/api/mcp", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  removeMcpServer: (name: string) =>
    request<{ servers: McpServer[]; message?: string }>(`/api/mcp/${encodeURIComponent(name)}`, {
      method: "DELETE",
    }),
  getSkills: () => request<{ skills: SkillInfo[] }>("/api/skills"),
  setSkillEnabled: (name: string, enabled: boolean) =>
    request<{ skills: SkillInfo[] }>("/api/skills", {
      method: "PUT",
      body: JSON.stringify({ name, enabled }),
    }),
  importSkill: (payload: { filename?: string; content?: string; url?: string }) =>
    request<{ skills: SkillInfo[]; message: string; source?: string }>("/api/skills/import", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getMemory: () => request<MemoryState>("/api/memory"),
  upsertMemory: (payload: {
    id?: string;
    key?: string;
    title?: string;
    content?: string;
    tags?: string[];
  }) =>
    request<MemoryState>("/api/memory", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  deleteMemory: (id: string) =>
    request<MemoryState>(`/api/memory/${encodeURIComponent(id)}`, { method: "DELETE" }),
  clearMemory: () => request<MemoryState>("/api/memory/clear", { method: "POST" }),
  toggleMemory: (enabled: boolean) =>
    request<MemoryState>("/api/memory/toggle", {
      method: "POST",
      body: JSON.stringify({ enabled }),
    }),
  getTelegram: () => request<TelegramConfig>("/api/telegram"),
  saveTelegram: (payload: {
    token?: string;
    bot_name?: string;
    workspace?: string;
    nemapi?: { host?: string; port?: number; model?: string };
  }) =>
    request<TelegramConfig>("/api/telegram", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getGithub: () => request<GithubConfig>("/api/github"),
  saveGithub: (payload: {
    token?: string;
    name?: string;
    email?: string;
    login?: string;
  }) =>
    request<GithubConfig>("/api/github", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
};
