import axios, { type AxiosInstance } from 'axios';
import { message } from 'antd';
import { MemoryClient } from '@agent-memory/sdk';

let globalErrorHandler: ((error: unknown) => void) | null = null;

/**
 * 注册全局错误处理回调（用于通知等场景）
 */
export function setGlobalErrorHandler(handler: (error: unknown) => void) {
  globalErrorHandler = handler;
}

const apiClient: AxiosInstance = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
});

// 请求拦截器 - 注入 JWT token + X-Workspace-Id
apiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  const wsId = localStorage.getItem('current_workspace_id');
  if (wsId) {
    config.headers['X-Workspace-Id'] = wsId;
  }
  return config;
});

// 响应拦截器 - 统一错误处理
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error.response?.status;
    const detail = error.response?.data?.detail || error.response?.data?.error || error.message;

    if (status === 401) {
      localStorage.removeItem('access_token');
      localStorage.removeItem('user');
      window.location.href = '/login';
    } else if (status && status >= 400) {
      // 非 401 错误显示通知
      const errorMsg = typeof detail === 'string' ? detail : `请求失败 (${status})`;
      message.error(errorMsg);
    }

    // 调用全局错误回调
    if (globalErrorHandler) {
      globalErrorHandler(error);
    }

    return Promise.reject(error);
  }
);

// ==================== SDK MemoryClient ====================
// 通过 SDK 提供的 MemoryClient 访问记忆相关 API
// SSE 硬编码 localhost 问题由 SDK baseUrl 配置化解决

function getSdkBaseUrl(): string {
  // 开发环境直连后端，生产环境使用相对路径
  const hostname = window.location.hostname;
  if (hostname === 'localhost' || hostname === '127.0.0.1') {
    return `http://${hostname}:8000`;
  }
  return '';
}

export function createMemoryClient(): MemoryClient {
  const token = localStorage.getItem('access_token') ?? undefined;
  const wsId = localStorage.getItem('current_workspace_id') ?? undefined;
  return new MemoryClient({
    baseUrl: getSdkBaseUrl(),
    token,
    workspaceId: wsId,
  });
}

// 全局 SDK 客户端实例（每次请求时重新读取 token/workspace）
export const memorySdk = {
  get client() {
    return createMemoryClient();
  },
};

// ==================== Auth API ====================
export const authApi = {
  register: (data: { username: string; password: string; email?: string }) =>
    apiClient.post('/auth/register', data),
  login: (data: { username: string; password: string }) =>
    apiClient.post('/auth/login', data),
  me: () => apiClient.get('/auth/me'),
  logout: () => apiClient.post('/auth/logout'),
};

// ==================== Memory Variables API ====================
export const variablesApi = {
  list: () => apiClient.get('/memory/variables', { params: { detailed: true } }),
  get: (key: string) => apiClient.get(`/memory/variables/${encodeURIComponent(key)}`),
  set: (key: string, value: unknown, ttl?: number) =>
    apiClient.post('/memory/variables', { key, value, ttl }),
  update: (key: string, value: unknown) =>
    apiClient.put(`/memory/variables/${encodeURIComponent(key)}`, { value }),
  updateTtl: (key: string, ttl: number | null) =>
    apiClient.put(`/memory/variables/${encodeURIComponent(key)}/ttl`, { ttl }),
  delete: (key: string) => apiClient.delete(`/memory/variables/${encodeURIComponent(key)}`),
  batchGet: (keys: string[]) => apiClient.post('/memory/variables/batch-get', { keys }),
  batchSet: (items: { key: string; value: unknown; ttl?: number }[]) =>
    apiClient.post('/memory/variables/batch-set', { items }),
};

// ==================== Memory Extraction API ====================
export const extractionApi = {
  extract: (text: string) =>
    apiClient.post('/memory/extraction/process', { user_input: text }),
  batchExtract: (conversations: { role: string; content: string; timestamp?: string }[]) =>
    apiClient.post('/memory/extraction/batch-extract', { conversation_history: conversations }),
  summary: (userId?: string) => apiClient.get('/memory/extraction/summary', { params: { user_id: userId } }),
  context: (userId?: string) => apiClient.get('/memory/extraction/context', { params: { user_id: userId } }),
  templates: () => apiClient.get('/memory/extraction/templates'),
};

// ==================== Memory Tables API ====================
export const tablesApi = {
  list: () => apiClient.get('/memory/tables/'),
  create: (data: { table_name: string; fields: { name: string; type: string }[]; description?: string }) =>
    apiClient.post('/memory/tables/', data),
  info: (tableName: string) => apiClient.get(`/memory/tables/${encodeURIComponent(tableName)}/info`),
  drop: (tableName: string) => apiClient.delete(`/memory/tables/${encodeURIComponent(tableName)}`),
  addRecord: (tableName: string, record: Record<string, unknown>) =>
    apiClient.post(`/memory/tables/${encodeURIComponent(tableName)}/records`, { record }),
  queryRecords: (tableName: string) =>
    apiClient.get(`/memory/tables/${encodeURIComponent(tableName)}/records`),
  queryWithFilters: (tableName: string, data: { filters?: Record<string, unknown>; order_by?: string; limit?: number; offset?: number }) =>
    apiClient.post(`/memory/tables/${encodeURIComponent(tableName)}/query`, data),
  updateRecord: (tableName: string, recordId: number, data: Record<string, unknown>) =>
    apiClient.put(`/memory/tables/${encodeURIComponent(tableName)}/records?record_id=${recordId}`, { updates: data }),
  deleteRecord: (tableName: string, recordId: number) =>
    apiClient.delete(`/memory/tables/${encodeURIComponent(tableName)}/records?record_id=${recordId}`),
  batchAdd: (tableName: string, records: Record<string, unknown>[]) =>
    apiClient.post(`/memory/tables/${encodeURIComponent(tableName)}/records/batch`, { records }),
  batchImport: (tableName: string, records: Record<string, unknown>[]) =>
    apiClient.post(`/memory/tables/${encodeURIComponent(tableName)}/records/batch`, { records }),
  naturalLanguageQuery: (query: string, _tableName?: string) =>
    apiClient.post('/memory/tables/nl-query', { question: query }),
  nlToSql: (tableName: string, query: string) =>
    apiClient.post(`/memory/tables/${encodeURIComponent(tableName)}/nl-to-sql`, { question: query }),
  executeSql: (tableName: string, sql: string) =>
    apiClient.post(`/memory/tables/${encodeURIComponent(tableName)}/execute-sql`, { sql }),
};

// ==================== Memory Fragments API ====================
export const fragmentsApi = {
  analyze: (history: string, userId?: string) =>
    apiClient.post('/memory/fragments/analyze', { history, user_id: userId }),
  summary: (history: string, userId?: string) =>
    apiClient.post('/memory/fragments/summary', { history, user_id: userId }),
  extract: (history: string, promptName?: string, userId?: string) =>
    apiClient.post('/memory/fragments/extract', { history, prompt_name: promptName, user_id: userId }),
  listPrompts: () => apiClient.get('/memory/fragments/prompts'),
  createPrompt: (data: { name: string; template: string; description?: string }) =>
    apiClient.post('/memory/fragments/prompts', data),
  list: (type?: string) => apiClient.get('/memory/fragments', { params: { type } }),
  create: (data: { type?: string; fragment_type?: string; content: string; importance_score?: number; ttl?: number }) =>
    apiClient.post('/memory/fragments', { fragment_type: data.fragment_type || data.type, content: data.content, importance_score: data.importance_score, ttl: data.ttl }),
  get: (fragmentId: number) => apiClient.get(`/memory/fragments/${fragmentId}`),
  update: (fragmentId: number, data: { content?: string; importance_score?: number; ttl?: number }) =>
    apiClient.put(`/memory/fragments/${fragmentId}`, data),
  delete: (fragmentId: number) => apiClient.delete(`/memory/fragments/${fragmentId}`),
  batchDelete: (fragmentIds: number[]) =>
    apiClient.delete('/memory/fragments/batch', { data: { fragment_ids: fragmentIds } }),
  cleanup: () => apiClient.post('/memory/fragments/cleanup'),
  semanticSearch: (query: string, topK?: number, threshold?: number) =>
    apiClient.post('/memory/fragments/search', { query, top_k: topK, threshold }),
};

// ==================== Auto Recall API ====================
export const recallApi = {
  auto: (query: string, userId?: string, topK?: number) =>
    apiClient.post('/memory/recall', { query, user_id: userId, top_k: topK }),
  summary: (userId?: string) => apiClient.post('/memory/recall/summary', { user_id: userId }),
  search: (query: string, topK?: number) =>
    apiClient.post('/memory/recall/search', { query, top_k: topK }),
  inject: (query: string, memories: unknown[]) =>
    apiClient.post('/memory/recall/inject', { query, memories }),
  config: () => apiClient.get('/memory/recall/config'),
  updateConfig: (data: Record<string, unknown>) => apiClient.put('/memory/recall/config', data),
  stats: () => apiClient.get('/memory/recall/stats'),
};

// ==================== Long-term Memory API ====================
export const longTermApi = {
  allMemories: (params?: { type?: string; page?: number; page_size?: number }) =>
    apiClient.get('/memory/long-term/memories', { params }),
  versionHistory: (memoryType: string, memoryId: string) =>
    apiClient.get('/memory/long-term/versions', { params: { memory_type: memoryType, memory_id: memoryId } }),
  recordVersion: (data: { memory_type: string; memory_id: string; change_type: string; change_data: unknown }) =>
    apiClient.post('/memory/long-term/versions', data),
  rollback: (memoryType: string, memoryId: string, versionId: number) =>
    apiClient.post('/memory/long-term/rollback', { memory_type: memoryType, memory_id: memoryId, version_id: versionId }),
  auditLog: (params?: { page?: number; page_size?: number }) =>
    apiClient.get('/memory/long-term/audit-log', { params }),
  feedback: (data: { memory_type: string; memory_id: string; feedback_type: string; value?: number }) =>
    apiClient.post('/memory/long-term/feedback', data),
  autoAdjust: () => apiClient.post('/memory/long-term/auto-adjust'),
  improvementStats: () => apiClient.get('/memory/long-term/improvement-stats'),
  batchDelete: (memories: { memory_type: string; memory_id: string }[]) =>
    apiClient.post('/memory/long-term/batch-delete', { memories }),
  adjustWeight: (data: { memory_type: string; memory_id: string; weight: number }) =>
    apiClient.post('/memory/long-term/adjust-weight', data),
};

// ==================== Agent API ====================
export const agentApi = {
  chat: (message: string, systemPrompt?: string, sessionId?: string) =>
    apiClient.post('/agent/chat', { message, system_prompt: systemPrompt, session_id: sessionId }),
  chatStream: (message: string, systemPrompt?: string, sessionId?: string) => {
    const token = localStorage.getItem('access_token');
    // SSE 流式请求直连后端，绕过 Vite proxy（http-proxy 不支持真正的流式转发）
    // baseUrl 由 SDK 统一配置化，不再硬编码 localhost
    const baseUrl = getSdkBaseUrl();
    return fetch(`${baseUrl}/api/v1/agent/chat/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: JSON.stringify({ message, system_prompt: systemPrompt, session_id: sessionId }),
    });
  },
  toolsSchema: () => apiClient.get('/agent/tools/schema'),
  tools: () => apiClient.post('/agent/tools'),
  executeTool: (toolName: string, parameters: Record<string, unknown>) =>
    apiClient.post(`/agent/tools/${encodeURIComponent(toolName)}/execute`, { parameters }),
  extract: (conversation: { role: string; content: string }[], autoStore?: boolean) =>
    apiClient.post('/agent/extract', { conversation, auto_store: autoStore }),
};

// ==================== System Integration API ====================
export const systemApi = {
  health: () => apiClient.get('/health'),
  // LLM Backend
  llmBackends: () => apiClient.get('/system/llm/backends'),
  registerLLMBackend: (data: { backend_name: string; backend_type: string; config: Record<string, unknown>; set_active?: boolean }) =>
    apiClient.post('/system/llm/backends', data),
  switchLLM: (backend: string) => apiClient.post('/system/llm/switch', { backend_name: backend }),
  setDefaultLLMBackend: (backend: string) => apiClient.put(`/system/llm/backends/${encodeURIComponent(backend)}/default`),
  deleteLLMBackend: (backend: string) => apiClient.delete(`/system/llm/backends/${encodeURIComponent(backend)}`),
  getLLMBackendDetails: (backend: string) => apiClient.get(`/system/llm/backends/${encodeURIComponent(backend)}`),
  checkLLMBackendHealth: (backend: string) => apiClient.get(`/system/llm/backends/${encodeURIComponent(backend)}/health`),
  llmStatus: () => apiClient.get('/system/llm/status'),
  // Plugins
  plugins: () => apiClient.get('/system/plugins'),
  registerPlugin: (data: { name: string; type: string; config: unknown }) =>
    apiClient.post('/system/plugins/register', data),
  unregisterPlugin: (pluginId: string) => apiClient.delete(`/system/plugins/${pluginId}`),
  // Performance
  performance: () => apiClient.get('/system/performance'),
  cacheStats: () => apiClient.get('/system/performance/cache'),
  clearCache: () => apiClient.post('/system/performance/cache/clear'),
  optimizeIndexes: () => apiClient.post('/system/performance/optimize-indexes'),
  // Security
  securityCheck: (input: string) => apiClient.post('/system/security/check', { input_string: input }),
  auditLog: (params?: { page?: number; page_size?: number }) =>
    apiClient.get('/system/security/audit-log', { params }),
  securityConfig: () => apiClient.get('/system/security/config'),
};

export default apiClient;

// ==================== Workspace API ====================
export interface Workspace {
  id: number;
  org_id: number;
  name: string;
  slug: string;
  kind: string;
  role?: string;
  joined_at?: string;
  created_at?: string;
}

export interface WorkspaceMember {
  id: number;
  workspace_id: number;
  user_id: number;
  role: string;
  joined_at?: string;
}

export const workspaceApi = {
  list: () => apiClient.get<Workspace[]>('/workspaces'),
  create: (data: { name: string; slug: string; kind?: string }) =>
    apiClient.post<Workspace>('/workspaces', data),
  get: (id: number) => apiClient.get<Workspace>(`/workspaces/${id}`),
  update: (id: number, data: { name?: string }) =>
    apiClient.put<Workspace>(`/workspaces/${id}`, data),
  addMember: (id: number, data: { user_id: number; role?: string }) =>
    apiClient.post<WorkspaceMember>(`/workspaces/${id}/members`, data),
  removeMember: (id: number, userId: number) =>
    apiClient.delete(`/workspaces/${id}/members/${userId}`),
  switchWorkspace: (workspaceId: number) =>
    apiClient.post('/workspaces/switch', { workspace_id: workspaceId }),
};

// ==================== API Key API ====================
export interface ApiKey {
  id: number;
  name: string;
  key_prefix: string;
  scopes: string[];
  last_used_at?: string;
  expires_at?: string;
  created_at: string;
}

export interface ApiKeyCreated {
  id: number;
  key: string; // 仅创建时返回明文
  name: string;
  expires_at?: string;
}

export const apiKeyApi = {
  list: () => apiClient.get<ApiKey[]>('/auth/api-keys'),
  create: (data: { name: string; scopes?: string[]; expires_at?: string }) =>
    apiClient.post<ApiKeyCreated>('/auth/api-keys', data),
  revoke: (keyId: number) => apiClient.delete(`/auth/api-keys/${keyId}`),
};

// ==================== Memory Observability API ====================
export const observabilityApi = {
  dashboard: () => apiClient.get('/memory/observability/dashboard'),
  metricsHistory: (days?: number) => apiClient.get('/memory/observability/metrics-history', { params: { days } }),
  snapshot: () => apiClient.post('/memory/observability/snapshot'),
  trace: (memoryId: string) => apiClient.get(`/memory/observability/trace/${encodeURIComponent(memoryId)}`),
  events: (params?: { event_type?: string; days?: number; limit?: number }) =>
    apiClient.get('/memory/observability/events', { params }),
  extractionTriggers: (limit?: number) => apiClient.get('/memory/observability/extraction-triggers', { params: { limit } }),
  evaluateAccuracy: (data: { memory_id: string; conversation_text?: string }) =>
    apiClient.post('/memory/observability/quality/evaluate', data),
  evaluateRelevance: (data: { query: string; fragments: { id: string; content: string }[] }) =>
    apiClient.post('/memory/observability/quality/relevance', data),
  batchEvaluate: (limit?: number) => apiClient.post('/memory/observability/quality/batch-evaluate', { limit }),
  qualityReport: (days?: number) => apiClient.get('/memory/observability/quality-report', { params: { days } }),
  // Performance
  performanceLatency: (hours?: number) => apiClient.get('/memory/observability/performance/latency', { params: { hours } }),
  performanceLlmCosts: (hours?: number) => apiClient.get('/memory/observability/performance/llm-costs', { params: { hours } }),
  performanceCache: (hours?: number) => apiClient.get('/memory/observability/performance/cache', { params: { hours } }),
  performanceErrors: (hours?: number) => apiClient.get('/memory/observability/performance/errors', { params: { hours } }),
};

// ==================== Memory Lifecycle API ====================
export const lifecycleApi = {
  stats: () => apiClient.get('/memory/lifecycle/stats'),
  halfLife: (fragmentType: string) => apiClient.get(`/memory/lifecycle/half-life/${fragmentType}`),
  coldList: () => apiClient.get('/memory/lifecycle/cold'),
  markCold: (data: { memory_type: string; memory_id: string; reason?: string }) =>
    apiClient.post('/memory/lifecycle/cold/mark', data),
  deletedList: () => apiClient.get('/memory/lifecycle/deleted'),
  softDelete: (type: string, id: string) => apiClient.post(`/memory/lifecycle/${type}/${id}/soft-delete`),
  restore: (type: string, id: string) => apiClient.post(`/memory/lifecycle/${type}/${id}/restore`),
  hardDelete: (type: string, id: string) => apiClient.post(`/memory/lifecycle/${type}/${id}/hard-delete`),
  archive: (type: string, id: string) => apiClient.post(`/memory/lifecycle/${type}/${id}/archive`),
  autoArchive: () => apiClient.post('/memory/lifecycle/auto-archive'),
  runCleanup: () => apiClient.post('/memory/lifecycle/run-cleanup'),
  findDuplicates: (content: string, threshold?: number) => apiClient.post('/memory/lifecycle/duplicates/find', { content, threshold: threshold || 0.85 }),
  mergeMemories: (sourceIds: number[], targetContent: string, targetType?: string) =>
    apiClient.post('/memory/lifecycle/duplicates/merge', { source_ids: sourceIds, target_content: targetContent, target_type: targetType || 'info' }),
  listConflicts: () => apiClient.get('/memory/lifecycle/conflicts'),
  detectConflicts: (key: string, newValue: string) => apiClient.post('/memory/lifecycle/conflicts/detect', { key, new_value: newValue }),
  resolveConflict: (conflictId: number, resolution: string, mergedValue?: string) =>
    apiClient.post(`/memory/lifecycle/conflicts/${conflictId}/resolve`, { resolution, merged_value: mergedValue }),
  deleteLog: () => apiClient.get('/memory/lifecycle/delete-log'),
  mergeLog: () => apiClient.get('/memory/lifecycle/merge-log'),
};

// ==================== Graph Memory API ====================
export const graphApi = {
  searchEntities: (query?: string) => apiClient.get('/memory/graph/entities', { params: { query } }),
  getEntity: (id: string) => apiClient.get(`/memory/graph/entities/${encodeURIComponent(id)}`),
  createEntity: (data: { name: string; entity_type: string; properties?: Record<string, unknown> }) =>
    apiClient.post('/memory/graph/entities', data),
  updateEntity: (id: string, data: { name?: string; entity_type?: string; metadata?: Record<string, unknown> }) =>
    apiClient.put(`/memory/graph/entities/${encodeURIComponent(id)}`, data),
  deleteEntity: (id: string) => apiClient.delete(`/memory/graph/entities/${encodeURIComponent(id)}`),
  mergeEntities: (sourceIds: string[], targetId: string) =>
    apiClient.post('/memory/graph/entities/merge', { source_entity_ids: sourceIds, target_entity_id: targetId }),
  listRelationships: (entityId?: string) => apiClient.get('/memory/graph/relationships', { params: { entity_id: entityId } }),
  createRelationship: (data: { source_entity_id: string; target_entity_id: string; relation_type: string; properties?: Record<string, unknown> }) =>
    apiClient.post('/memory/graph/relationships', data),
  deactivateRelationship: (id: string) => apiClient.delete(`/memory/graph/relationships/${encodeURIComponent(id)}`),
  getNeighbors: (entityId: string, depth?: number) => apiClient.get('/memory/graph/neighbors', { params: { entity_id: entityId, depth } }),
  extractEntities: (text: string) => apiClient.post('/memory/graph/extract', { text }),
  queryGraph: (query: string) => apiClient.get('/memory/graph/query', { params: { q: query } }),
  getHistory: (entityId: string) => apiClient.get('/memory/graph/history', { params: { entity_id: entityId } }),
  getStatistics: () => apiClient.get('/memory/graph/statistics'),
  getDuplicates: (threshold?: number) => apiClient.get('/memory/graph/duplicates', { params: { threshold: threshold || 3 } }),
};

// ==================== Hybrid Search API ====================
export const hybridSearchApi = {
  search: (data: { query: string; alpha?: number; beta?: number; gamma?: number; delta?: number; top_k?: number }) =>
    apiClient.post('/memory/hybrid-search', data),
  bm25: (data: { query: string; top_k?: number }) =>
    apiClient.post('/memory/hybrid-search/bm25', data),
  rerank: (data: { query: string; fragments: { id: string; content: string }[] }) =>
    apiClient.post('/memory/hybrid-search/rerank', data),
  config: () => apiClient.get('/memory/hybrid-search/config'),
  updateConfig: (data: Record<string, unknown>) =>
    apiClient.put('/memory/hybrid-search/config', data),
  rebuildIndex: () => apiClient.post('/memory/hybrid-search/rebuild-index'),
};

// ==================== Sessions API ====================
export interface Session {
  id: number;
  session_id: string;
  user_id: number;
  title: string;
  message_count: number;
  created_at: string;
  updated_at: string;
  highlights?: string[];
  title_matched?: boolean;
}

export interface SessionListResponse {
  success: boolean;
  sessions: Session[];
  count: number;
  query?: string;
}

export const sessionsApi = {
  list: (params?: { limit?: number; offset?: number; page?: number; page_size?: number }) =>
    apiClient.get<SessionListResponse>('/agent/sessions', { params: params || {} }),
  search: (q: string, params?: { page?: number; page_size?: number; highlight_length?: number }) =>
    apiClient.get<SessionListResponse>('/agent/sessions/search', { params: { q, page_size: 20, ...params } }),
  batchDelete: (sessionIds: string[]) =>
    apiClient.delete('/agent/sessions/batch', { data: { session_ids: sessionIds } }),
  get: (sessionId: string) => apiClient.get(`/agent/sessions/${encodeURIComponent(sessionId)}`),
  delete: (sessionId: string) => apiClient.delete(`/agent/sessions/${encodeURIComponent(sessionId)}`),
  rename: (sessionId: string, title: string) =>
    apiClient.put(`/agent/sessions/${encodeURIComponent(sessionId)}/title`, { title }),
  messages: (sessionId: string, limit?: number, offset?: number) =>
    apiClient.get(`/agent/sessions/${encodeURIComponent(sessionId)}/messages`, { params: { limit: limit || 100, offset: offset || 0 } }),
  // 摘要相关
  getSummary: (sessionId: string) =>
    apiClient.get(`/agent/sessions/${encodeURIComponent(sessionId)}/summary`),
  updateSummary: (sessionId: string, summary: string) =>
    apiClient.put(`/agent/sessions/${encodeURIComponent(sessionId)}/summary`, { summary }),
  getSummaryHistory: (sessionId: string, limit?: number) =>
    apiClient.get(`/agent/sessions/${encodeURIComponent(sessionId)}/summary/history`, { params: { limit: limit || 20 } }),
  regenerateSummary: (sessionId: string) =>
    apiClient.post(`/agent/sessions/${encodeURIComponent(sessionId)}/summary/regenerate`),
};
