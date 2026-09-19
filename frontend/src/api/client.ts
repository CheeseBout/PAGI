const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

export class ApiError extends Error {
  status: number;
  code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function request<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const res = await fetch(API_BASE + path, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  if (res.status === 204) return null as T;
  const text = await res.text();
  const body = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const e = body?.error ?? {};
    throw new ApiError(res.status, e.code ?? "error", e.message ?? res.statusText);
  }
  return body as T;
}

export interface Agent {
  id: string;
  name: string;
  system_prompt?: string;
  provider: string;
  model: string;
  is_default: boolean;
  vision_enabled?: boolean;
  temperature?: number;
  max_tokens?: number;
  tools_allowed: string[];
  tool_policy: Record<string, string>;
  kb_collection_ids?: string[];
  rag_config?: Record<string, unknown>;
  is_delegatable?: boolean;
  delegate_description?: string;
  orchestration?: Record<string, unknown>;
  // ── 2D avatar (SPEC §20.2) ─────────────────────────────────────────
  avatar_config?: Record<string, unknown>;
  /** derived: enabled=true AND model_path resolves to a real file right now
   * (SPEC §20.5). Recomputed server-side every GET, never cached. */
  avatar_ready?: boolean;
}

export interface OpenRouterModel {
  id: string;
  name: string;
  context_length?: number;
}

export type AgentInput = {
  name: string;
  system_prompt: string;
  provider: string;
  model: string;
  tools_allowed: string[];
  tool_policy: Record<string, string>;
  temperature: number;
  max_tokens: number;
  is_default: boolean;
  vision_enabled?: boolean;
  kb_collection_ids?: string[];
  rag_config?: Record<string, unknown>;
  is_delegatable?: boolean;
  delegate_description?: string;
  orchestration?: Record<string, unknown>;
  avatar_config?: Record<string, unknown>;
};

// ── Config schema forms (Phase 15, SPEC §17) ───────────────────────
export interface ConfigFieldMeta {
  group: string;
  label: string;
  help: string;
  order?: number;
  requires_reingest?: boolean;
  cost_hint?: string | null;
  widget?: string;
  depends_on?: Record<string, unknown>;
}
export interface ConfigSchema {
  json_schema: {
    properties: Record<
      string,
      { type?: string; enum?: string[]; minimum?: number; maximum?: number; description?: string }
    >;
  };
  fields: Record<string, ConfigFieldMeta>;
  groups: { id: string; label: string; order: number; note?: string }[];
  defaults: Record<string, unknown>;
}
export interface PatternInfo {
  id: string;
  label: string;
  summary: string;
  cost_tier: string;
  requires_delegation: boolean;
  calls_hint: string;
  params: string[];
}
export interface DelegatableAgent {
  id: string;
  name: string;
  delegate_description: string;
  provider: string;
  model: string;
}

// ── Sub-agent tree + pattern run timeline (Phase 13 / 14) ──────────
export interface SubAgentNode {
  session_id: string;
  parent_session_id: string | null;
  depth: number;
  agent_id: string;
  agent_name: string;
  delegated_task: string | null;
  status: string;
  tokens: number;
  cost_usd: number;
}
export interface AgentRunRow {
  id: string;
  session_id: string;
  root_session_id: string | null;
  message_id: string | null;
  pattern: string;
  step_no: number;
  node: string;
  attempt: number;
  input_summary: string | null;
  output_summary: string | null;
  payload: Record<string, unknown>;
  status: string;
  latency_ms: number;
  cost_usd: number | null;
  error: string | null;
  created_at: string;
}

// ── Agent / trajectory evaluation (Phase 16) ──────────────────────
export interface AgentEvalCaseInput {
  prompt: string;
  expected_outcome?: string | null;
  optimal_steps?: number | null;
  forbidden_tools?: string[];
  // held out of Weakness Miner's input pool (Phase 17) — a fair regression
  // check needs cases the miner never saw.
  held_out?: boolean;
}
export interface AgentEvalRun {
  id: string;
  name: string;
  agent_id: string;
  pattern: string | null;
  judge_model: string | null;
  case_count: number;
  task_resolution_rate: number | null;
  step_efficiency: number | null;
  redundant_tool_rate: number | null;
  parameter_hallucination_rate: number | null;
  forbidden_tool_rate: number | null;
  avg_llm_calls: number | null;
  avg_cost_usd: number | null;
  avg_latency_ms: number | null;
  status: string;
  created_at: string;
}
export interface AgentEvalCase extends AgentEvalCaseInput {
  id: string;
  run_id: string;
  answer: string | null;
  resolved: boolean | null;
  steps_taken: number | null;
  redundant_tool_calls: number | null;
  forbidden_tool_used: boolean | null;
  parameter_hallucination: boolean | null;
  llm_calls: number | null;
  cost_usd: number | null;
  latency_ms: number | null;
  judge_rationale: Record<string, unknown>;
}

// ── Harness self-improvement (Phase 17) ──────────────────────────────
export interface WeaknessReport {
  id: string;
  agent_id: string;
  agent_eval_run_id: string;
  pattern: string;
  example_case_ids: string[];
  created_at: string;
}
// ── Multi-day projects (Phase 18) ────────────────────────────────────
export interface ProjectRun {
  id: string;
  name: string;
  planner_agent_id: string;
  developer_agent_id: string;
  qa_agent_id: string;
  root_session_id: string;
  workspace_path: string;
  status: "active" | "paused" | "done";
  max_iterations: number;
  iterations_done: number;
  budget_usd: number;
  spent_usd: number;
  qa_fail_streak: number;
  qa_fail_pause_threshold: number;
  schedule: string | null;
  created_at: string;
  updated_at: string;
}
export interface ProjectIteration {
  id: string;
  project_run_id: string;
  iteration_no: number;
  planner_session_id: string | null;
  developer_session_id: string | null;
  qa_session_id: string | null;
  workspace_commit_sha: string | null;
  qa_verdict: "pass" | "fail" | null;
  qa_reason: string | null;
  cost_usd: number;
  status: "running" | "done" | "qa_failed" | "error";
  error: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface AgentConfigVersion {
  id: string;
  agent_id: string;
  parent_version_id: string | null;
  weakness_report_id: string | null;
  diff: Record<string, unknown>;
  config_snapshot: Record<string, unknown>;
  rationale: string;
  source_eval_run_id: string | null;
  status: "proposed" | "rejected" | "active" | "superseded";
  held_in_score: number | null;
  held_out_score: number | null;
  reject_reason: string | null;
  created_at: string;
  activated_at: string | null;
}

// ── RAG / Knowledge Base (Phase 12) ─────────────────────────────────
export interface KbCollection {
  id: string;
  name: string;
  description: string;
  config: Record<string, unknown>;
  embedding_model: string;
  embedding_dim: number | null;
  doc_count: number;
  chunk_count: number;
  status: string;
  config_resolved?: Record<string, unknown>;
}

export interface KbDocument {
  id: string;
  collection_id: string;
  source_type: string;
  source_uri: string;
  title: string;
  version: number;
  status: string;
  error: string | null;
  token_count: number | null;
  injection_flagged: boolean;
}

export interface KbStage {
  name: string;
  duration_ms: number;
  candidate_count: number;
  skipped?: boolean;
  method?: string;
  mode?: string;
  removed?: number;
  detail?: string;
  context_tokens?: number;
  verdict?: string;
  score?: number;
  fallback?: string;
  queries?: { kind: string; text: string }[];
  top?: { chunk_id: string; score: number }[];
}

export interface KbSearchResult {
  chunks: {
    chunk_id: string;
    document_id: string;
    title: string;
    source_uri: string;
    text: string;
    score: number;
  }[];
  context: string;
  query_log_id: string | null;
  no_context: boolean;
  reason: string | null;
  total_ms: number;
  citations?: RagCitation[];
  stages?: KbStage[];
  config_resolved?: Record<string, unknown>;
}

export interface RagCitation {
  chunk_id: string;
  document_id: string;
  title: string;
  source_uri: string;
  score?: number;
  updated_at?: string | null;
}

export interface RagInfo {
  query_log_id: string | null;
  citations: RagCitation[];
  no_context: boolean;
  reason: string | null;
}

export interface KbQueryLog {
  id: string;
  session_id: string | null;
  message_id: string | null;
  collection_ids: string[];
  query_raw: string;
  queries_used: { kind: string; text: string }[];
  config_snapshot: Record<string, unknown>;
  stages: KbStage[];
  picked_chunk_ids: string[];
  context_tokens: number;
  total_ms: number;
  created_at: string;
}

export interface KbChunkDetail {
  id: string;
  document_id: string;
  ordinal: number;
  text: string;
  token_count: number;
  meta: Record<string, unknown>;
  document: { id: string; title: string; source_uri: string; source_type: string } | null;
}

export interface KbEvalCaseInput {
  question: string;
  ground_truth: string | null;
}

export interface KbEvalRun {
  id: string;
  collection_id: string;
  name: string;
  config_snapshot: Record<string, unknown>;
  agent_id: string | null;
  judge_model: string | null;
  case_count: number;
  faithfulness: number | null;
  answer_relevancy: number | null;
  context_precision: number | null;
  context_recall: number | null;
  status: string;
  created_at: string;
}

export interface KbEvalCase extends KbEvalCaseInput {
  id: string;
  run_id: string;
  answer: string | null;
  contexts: string[];
  faithfulness: number | null;
  answer_relevancy: number | null;
  context_precision: number | null;
  context_recall: number | null;
  judge_rationale: Record<string, unknown>;
}

export interface CronJob {
  id: string;
  agent_id: string;
  name: string;
  schedule: string;
  prompt: string;
  unattended_allowed_tools: string[];
  enabled: boolean;
  last_run_at: string | null;
  next_run_at: string | null;
}

export interface McpServer {
  id: string;
  name: string;
  transport: "stdio" | "sse" | "http";
  config: Record<string, unknown>;
  enabled: boolean;
}

export interface Grant {
  id: string;
  session_id: string;
  tool_name: string;
  created_at: string;
}

export interface UsageBucket {
  bucket: string;
  calls: number;
  tokens_in: number;
  tokens_out: number;
  cached_tokens: number;
  cost_usd: number;
  errors: number;
  cache_hits: number;
}
export interface UsageReport {
  group_by: string;
  buckets: UsageBucket[];
  totals: Omit<UsageBucket, "bucket" | "cache_hits">;
  by_kind?: { kind: string; calls: number; cost_usd: number }[];
  by_origin?: { origin: "chat" | "subagent"; calls: number; cost_usd: number }[];
}
export interface BudgetReport {
  month: string;
  spent_usd: number;
  budget_usd: number;
  pct: number | null;
  over: boolean;
}

export interface WorkspaceEntry {
  name: string;
  type: "file" | "dir";
  size_bytes: number;
}

export interface ConversationSummary {
  id: string;
  title: string | null;
  agent_id: string;
  updated_at: string;
}

export interface Attachment {
  id: string;
  kind: "image" | "file";
  filename: string;
  content_type: string;
  size_bytes: number;
  workspace_path?: string;
}

export interface ChatMessage {
  id: string;
  session_id?: string;
  role: "user" | "assistant" | "tool" | "system";
  content: string | null;
  tool_calls?: { id: string; name: string; args: Record<string, unknown> }[] | null;
  tool_call_id?: string | null;
  attachments?: Attachment[] | null;
  rag?: RagInfo | null;
  tokens_in?: number | null;
  tokens_out?: number | null;
  /** From the `chat`-kind Trace row for this message — absent when there
   * isn't one yet (message just streamed locally) or ever wasn't one. */
  cost_usd?: number | null;
  latency_ms?: number | null;
  created_at: string;
}

export interface DiffPreview {
  path: string;
  old: string | null;
  new: string | null;
  truncated: boolean;
}

export interface Approval {
  id: string;
  session_id: string;
  tool_call_id: string;
  tool_name: string;
  tool_args: Record<string, unknown> & { _preview?: DiffPreview };
  status: string;
  // present when the approval belongs to a sub-agent (Phase 13, SPEC §15.4)
  sub_session_id?: string;
  agent_name?: string;
}

/** Shared by the per-agent and shared-library avatar upload endpoints — both
 * take the same `<input webkitdirectory>` FileList and return the same shape. */
async function uploadAvatarFolder(
  path: string,
  files: FileList | File[],
): Promise<{ model_path: string; models: string[] }> {
  const fd = new FormData();
  for (const file of Array.from(files)) {
    // `<input webkitdirectory>` sets this to the path relative to the picked
    // folder (e.g. "hiyori/Hiyori.model3.json") — the field name IS that
    // path; the backend reads the form generically instead of expecting a
    // fixed "files" key (routes_agents.py::upload_avatar_model /
    // routes_avatar_library.py::upload_to_avatar_library).
    const rel = (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
    fd.append(rel, file, file.name);
  }
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    credentials: "include",
    body: fd,
  });
  const text = await res.text();
  const body = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const e = body?.error ?? {};
    throw new ApiError(res.status, e.code ?? "error", e.message ?? res.statusText);
  }
  return body as { model_path: string; models: string[] };
}

export const api = {
  login: (username: string, password: string) =>
    request<{ user: { id: string; username: string } }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  logout: () => request<null>("/auth/logout", { method: "POST" }),
  me: () => request<{ id: string; username: string }>("/auth/me"),

  listAgents: () => request<Agent[]>("/agents"),

  // ── config-schema forms + patterns (Phase 15) ─────────────────────
  configSchema: (which: "rag" | "orchestration") =>
    request<ConfigSchema>(`/meta/config-schema/${which}`),
  listPatterns: () => request<PatternInfo[]>("/meta/patterns"),
  listDelegatableAgents: () => request<DelegatableAgent[]>("/meta/delegatable-agents"),

  // ── sub-agent tree + pattern timeline (Phase 13 / 14) ────────────
  conversationTree: (id: string) =>
    request<{ root: string; nodes: SubAgentNode[] }>(`/conversations/${id}/tree`),
  conversationRuns: (id: string, messageId?: string) =>
    request<AgentRunRow[]>(
      `/conversations/${id}/runs${messageId ? `?message_id=${messageId}` : ""}`,
    ),

  // ── agent / trajectory eval (Phase 16) ──────────────────────────
  agentEvalRuns: (agentId?: string) =>
    request<AgentEvalRun[]>(
      `/agent-eval/runs${agentId ? `?agent_id=${agentId}` : ""}`,
    ),
  createAgentEvalRun: (body: {
    name: string;
    agent_id: string;
    pattern?: string | null;
    judge_model?: string | null;
    cases: AgentEvalCaseInput[];
  }) => request<AgentEvalRun>("/agent-eval/runs", { method: "POST", body: JSON.stringify(body) }),
  synthAgentEvalCases: (agent_id: string, n = 8) =>
    request<{ cases: AgentEvalCaseInput[] }>("/agent-eval/synth", {
      method: "POST",
      body: JSON.stringify({ agent_id, n }),
    }),
  agentEvalRun: (id: string) =>
    request<{ run: AgentEvalRun; cases: AgentEvalCase[] }>(`/agent-eval/runs/${id}`),
  deleteAgentEvalRun: (id: string) =>
    request<null>(`/agent-eval/runs/${id}`, { method: "DELETE" }),
  runHarnessCycle: (runId: string) =>
    request<{ scheduled: boolean; run_id: string }>(`/agent-eval/runs/${runId}/harness/run`, {
      method: "POST",
    }),

  // ── harness self-improvement (Phase 17) ──────────────────────────
  listAgentConfigVersions: (agentId: string) =>
    request<AgentConfigVersion[]>(`/agents/${agentId}/config-versions`),
  listWeaknessReports: (agentId: string) =>
    request<WeaknessReport[]>(`/agents/${agentId}/weakness-reports`),
  rollbackAgentConfigVersion: (agentId: string, versionId: string) =>
    request<AgentConfigVersion>(
      `/agents/${agentId}/config-versions/${versionId}/rollback`,
      { method: "POST" },
    ),

  listConversations: (before?: string) =>
    request<ConversationSummary[]>(`/conversations${before ? `?before=${encodeURIComponent(before)}` : ""}`),
  createConversation: (agent_id: string) =>
    request<{ id: string }>("/conversations", {
      method: "POST",
      body: JSON.stringify({ agent_id }),
    }),
  getConversation: (id: string, before?: string) =>
    request<{ session: { id: string; title: string | null; agent_id: string }; messages: ChatMessage[] }>(
      `/conversations/${id}${before ? `?before=${encodeURIComponent(before)}` : ""}`,
    ),
  renameConversation: (id: string, title: string) =>
    request(`/conversations/${id}`, { method: "PATCH", body: JSON.stringify({ title }) }),
  deleteConversation: (id: string) => request<null>(`/conversations/${id}`, { method: "DELETE" }),

  listPendingApprovals: () => request<Approval[]>("/approvals?status=pending"),
  approve: (id: string, remember?: "session") =>
    request<Approval>(`/approvals/${id}/approve${remember ? `?remember=${remember}` : ""}`, {
      method: "POST",
    }),
  deny: (id: string) => request<Approval>(`/approvals/${id}/deny`, { method: "POST" }),

  // ── "always allow" grants (Wave 4a) ────────────────────────────────
  listGrants: (sessionId: string) => request<Grant[]>(`/conversations/${sessionId}/grants`),
  revokeGrant: (sessionId: string, tool: string) =>
    request<null>(`/conversations/${sessionId}/grants/${tool}`, { method: "DELETE" }),

  // ── workspace browser (Wave 4c) ───────────────────────────────────
  listWorkspace: (sessionId: string, path?: string) =>
    request<{ entries: WorkspaceEntry[] }>(
      `/conversations/${sessionId}/workspace${path ? `?path=${encodeURIComponent(path)}` : ""}`,
    ),
  readWorkspaceFile: (sessionId: string, path: string) =>
    request<{ path: string; content: string; size_bytes: number | null; truncated: boolean }>(
      `/conversations/${sessionId}/workspace/file?path=${encodeURIComponent(path)}`,
    ),
  deleteWorkspaceFile: (sessionId: string, path: string) =>
    request<null>(
      `/conversations/${sessionId}/workspace?path=${encodeURIComponent(path)}`,
      { method: "DELETE" },
    ),

  // ── settings CRUD (Wave 4d) ───────────────────────────────────────
  // 2D avatar models: an agent's picker merges its own directory with the
  // shared library server-side (`GET /agents/{id}/avatar-models`); the
  // shared-library endpoints below aren't agent-scoped at all, so they work
  // even for a not-yet-saved agent.
  listAvatarModels: (agentId: string) =>
    request<{ models: string[] }>(`/agents/${agentId}/avatar-models`),
  uploadAvatarModel: (agentId: string, files: FileList | File[]) =>
    uploadAvatarFolder(`/agents/${agentId}/avatar/upload`, files),
  listSharedAvatarModels: () => request<{ models: string[] }>("/avatar-library"),
  uploadSharedAvatarModel: (files: FileList | File[]) =>
    uploadAvatarFolder("/avatar-library/upload", files),
  createAgent: (body: AgentInput) =>
    request<Agent>("/agents", { method: "POST", body: JSON.stringify(body) }),
  updateAgent: (id: string, body: Partial<AgentInput>) =>
    request<Agent>(`/agents/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteAgent: (id: string) => request<null>(`/agents/${id}`, { method: "DELETE" }),

  listCronJobs: () => request<CronJob[]>("/cron-jobs"),
  createCronJob: (body: Partial<CronJob>) =>
    request<CronJob>("/cron-jobs", { method: "POST", body: JSON.stringify(body) }),
  updateCronJob: (id: string, body: Partial<CronJob>) =>
    request<CronJob>(`/cron-jobs/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteCronJob: (id: string) => request<null>(`/cron-jobs/${id}`, { method: "DELETE" }),
  runCronJob: (id: string) =>
    request<{ session_id: string }>(`/cron-jobs/${id}/run-now`, { method: "POST" }),

  // ── multi-day projects (Phase 18) ────────────────────────────────
  listProjects: () => request<ProjectRun[]>("/projects"),
  createProject: (body: {
    name: string;
    planner_agent_id: string;
    developer_agent_id: string;
    qa_agent_id: string;
    max_iterations?: number;
    budget_usd?: number;
    schedule?: string | null;
  }) => request<ProjectRun>("/projects", { method: "POST", body: JSON.stringify(body) }),
  getProject: (id: string) =>
    request<{ run: ProjectRun; iterations: ProjectIteration[] }>(`/projects/${id}`),
  patchProject: (
    id: string,
    body: Partial<Pick<ProjectRun, "max_iterations" | "budget_usd" | "schedule">>,
  ) => request<ProjectRun>(`/projects/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  pauseProject: (id: string) => request<ProjectRun>(`/projects/${id}/pause`, { method: "POST" }),
  resumeProject: (id: string) => request<ProjectRun>(`/projects/${id}/resume`, { method: "POST" }),
  iterateProjectNow: (id: string) =>
    request<{ scheduled: boolean; project_id: string }>(`/projects/${id}/iterate-now`, {
      method: "POST",
    }),
  deleteProject: (id: string) => request<null>(`/projects/${id}`, { method: "DELETE" }),

  listMcpServers: () => request<McpServer[]>("/mcp-servers"),
  createMcpServer: (body: Partial<McpServer>) =>
    request<McpServer>("/mcp-servers", { method: "POST", body: JSON.stringify(body) }),
  updateMcpServer: (id: string, body: Partial<McpServer>) =>
    request<McpServer>(`/mcp-servers/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteMcpServer: (id: string) => request<null>(`/mcp-servers/${id}`, { method: "DELETE" }),

  // ── Knowledge Base / RAG (Phase 12) ───────────────────────────────
  listKbCollections: () => request<KbCollection[]>("/kb/collections"),
  getKbCollection: (id: string) => request<KbCollection>(`/kb/collections/${id}`),
  createKbCollection: (body: { name: string; description?: string; embedding_model?: string; config?: Record<string, unknown> }) =>
    request<KbCollection>("/kb/collections", { method: "POST", body: JSON.stringify(body) }),
  updateKbCollection: (id: string, body: { name?: string; description?: string; config?: Record<string, unknown> }) =>
    request<KbCollection>(`/kb/collections/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteKbCollection: (id: string) => request<null>(`/kb/collections/${id}`, { method: "DELETE" }),

  listKbDocuments: (collectionId: string) =>
    request<KbDocument[]>(`/kb/collections/${collectionId}/documents`),
  addKbText: (collectionId: string, title: string, text: string) =>
    request<KbDocument>(`/kb/collections/${collectionId}/documents/from-text`, {
      method: "POST",
      body: JSON.stringify({ title, text }),
    }),
  addKbUrl: (collectionId: string, url: string, title?: string) =>
    request<KbDocument>(`/kb/collections/${collectionId}/documents/from-url`, {
      method: "POST",
      body: JSON.stringify({ url, title }),
    }),
  reingestKbDocument: (docId: string, force = false) =>
    request<KbDocument>(`/kb/documents/${docId}/reingest`, {
      method: "POST",
      body: JSON.stringify({ force }),
    }),
  deleteKbDocument: (docId: string) =>
    request<null>(`/kb/documents/${docId}`, { method: "DELETE" }),
  kbSearch: (
    query: string,
    collectionIds: string[],
    explain = false,
    config?: Record<string, unknown>,
  ) =>
    request<KbSearchResult>("/kb/search", {
      method: "POST",
      body: JSON.stringify({ query, collection_ids: collectionIds, explain, config }),
    }),
  kbQueryLogs: (sessionId?: string) =>
    request<KbQueryLog[]>(`/kb/query-logs${sessionId ? `?session_id=${sessionId}` : ""}`),
  kbQueryLog: (id: string) => request<KbQueryLog>(`/kb/query-logs/${id}`),
  kbChunk: (chunkId: string) => request<KbChunkDetail>(`/kb/chunks/${chunkId}`),

  kbEvalGenerate: (collectionId: string, n: number, model?: string) =>
    request<{ cases: KbEvalCaseInput[] }>(`/kb/collections/${collectionId}/eval/generate`, {
      method: "POST",
      body: JSON.stringify({ n, model }),
    }),
  kbEvalRuns: (collectionId: string) =>
    request<KbEvalRun[]>(`/kb/eval-runs?collection_id=${collectionId}`),
  kbCreateEvalRun: (body: {
    collection_id: string;
    name: string;
    agent_id?: string;
    cases: KbEvalCaseInput[];
    config?: Record<string, unknown>;
    judge_model?: string;
  }) => request<KbEvalRun>("/kb/eval-runs", { method: "POST", body: JSON.stringify(body) }),
  kbEvalRun: (id: string) =>
    request<{ run: KbEvalRun; cases: KbEvalCase[] }>(`/kb/eval-runs/${id}`),
  kbDeleteEvalRun: (id: string) => request<null>(`/kb/eval-runs/${id}`, { method: "DELETE" }),
  uploadKbDocument: async (collectionId: string, file: File): Promise<KbDocument> => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(`${API_BASE}/kb/collections/${collectionId}/documents`, {
      method: "POST",
      credentials: "include",
      body: fd,
    });
    const text = await res.text();
    const body = text ? JSON.parse(text) : null;
    if (!res.ok) {
      const e = body?.error ?? {};
      throw new ApiError(res.status, e.code ?? "error", e.message ?? res.statusText);
    }
    return body as KbDocument;
  },

  // ── usage / cost (Wave 4e) ────────────────────────────────────────
  usage: (groupBy: "day" | "model" | "session" | "kind", since?: string) =>
    request<UsageReport>(
      `/admin/usage?group_by=${groupBy}${since ? `&since=${encodeURIComponent(since)}` : ""}`,
    ),
  usageBudget: () => request<BudgetReport>("/admin/usage/budget"),
  modelPricing: (model: string, provider?: string) =>
    request<{ model: string; input_per_1m?: number; output_per_1m?: number; source?: string; known?: boolean }>(
      `/admin/model-pricing?model=${encodeURIComponent(model)}${provider ? `&provider=${provider}` : ""}`,
    ),
  openrouterModels: () => request<OpenRouterModel[]>("/meta/openrouter-models"),

  uploadFile: async (
    sessionId: string,
    file: File,
    target: "attachment" | "workspace" = "attachment",
  ): Promise<Attachment> => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("target", target);
    const res = await fetch(`${API_BASE}/conversations/${sessionId}/files`, {
      method: "POST",
      credentials: "include",
      body: fd,
    });
    const text = await res.text();
    const body = text ? JSON.parse(text) : null;
    if (!res.ok) {
      const e = body?.error ?? {};
      throw new ApiError(res.status, e.code ?? "error", e.message ?? res.statusText);
    }
    return body as Attachment;
  },
  fileUrl: (sessionId: string, attachmentId: string) =>
    `${API_BASE}/conversations/${sessionId}/files/${attachmentId}`,
  deleteFile: (sessionId: string, attachmentId: string) =>
    request<null>(`/conversations/${sessionId}/files/${attachmentId}`, { method: "DELETE" }),
};
