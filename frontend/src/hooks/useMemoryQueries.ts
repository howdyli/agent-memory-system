import { useQuery, useMutation, useQueryClient, useInfiniteQuery } from '@tanstack/react-query';
import {
  variablesApi,
  tablesApi,
  fragmentsApi,
  observabilityApi,
  graphApi,
  systemApi,
  recallApi,
  sessionsApi,
  hybridSearchApi,
  lifecycleApi,
} from '../services/api';

// ==================== Memory Variables ====================
export function useVariables() {
  return useQuery({
    queryKey: ['variables'],
    queryFn: () => variablesApi.list().then(r => r.data?.variables || r.data || []),
  });
}

// ==================== Memory Tables ====================
export function useTables() {
  return useQuery({
    queryKey: ['tables'],
    queryFn: () => tablesApi.list().then(r => r.data?.tables || r.data || []),
  });
}

export function useTableRecords(tableName: string) {
  return useQuery({
    queryKey: ['tables', tableName, 'records'],
    queryFn: () => tablesApi.queryRecords(tableName).then(r => r.data?.records || r.data || []),
    enabled: !!tableName,
  });
}

export function useAddRecord(tableName: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (record: Record<string, unknown>) => tablesApi.addRecord(tableName, record),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['tables', tableName, 'records'] }); },
  });
}

// ==================== Memory Fragments ====================
export function useFragments(type?: string) {
  return useQuery({
    queryKey: ['fragments', type],
    queryFn: () => fragmentsApi.list(type).then(r => r.data?.fragments || r.data || []),
  });
}

// ==================== Observability Dashboard ====================
export function useObservabilityDashboard() {
  return useQuery({
    queryKey: ['observability', 'dashboard'],
    queryFn: () => observabilityApi.dashboard().then(r => r.data),
    staleTime: 60_000, // 1 minute
  });
}

export function useMetricsHistory(days?: number) {
  return useQuery({
    queryKey: ['observability', 'metrics', days],
    queryFn: () => observabilityApi.metricsHistory(days).then(r => r.data),
  });
}

// ==================== Performance Metrics ====================
export function usePerformanceLatency(hours?: number) {
  return useQuery({
    queryKey: ['observability', 'performance', 'latency', hours],
    queryFn: () => observabilityApi.performanceLatency(hours).then(r => r.data),
    refetchInterval: 30_000,
    staleTime: 30_000,
  });
}

export function usePerformanceLlmCosts(hours?: number) {
  return useQuery({
    queryKey: ['observability', 'performance', 'llm-costs', hours],
    queryFn: () => observabilityApi.performanceLlmCosts(hours).then(r => r.data),
    refetchInterval: 30_000,
    staleTime: 30_000,
  });
}

export function usePerformanceCache(hours?: number) {
  return useQuery({
    queryKey: ['observability', 'performance', 'cache', hours],
    queryFn: () => observabilityApi.performanceCache(hours).then(r => r.data),
    refetchInterval: 30_000,
    staleTime: 30_000,
  });
}

export function usePerformanceErrors(hours?: number) {
  return useQuery({
    queryKey: ['observability', 'performance', 'errors', hours],
    queryFn: () => observabilityApi.performanceErrors(hours).then(r => r.data),
    refetchInterval: 30_000,
    staleTime: 30_000,
  });
}

// ==================== Graph Entities ====================
export function useGraphEntities(query?: string) {
  return useQuery({
    queryKey: ['graph', 'entities', query],
    queryFn: () => graphApi.searchEntities(query).then(r => r.data?.entities || r.data || []),
  });
}

export function useGraphRelationships(entityId?: string) {
  return useQuery({
    queryKey: ['graph', 'relationships', entityId],
    queryFn: () => graphApi.listRelationships(entityId).then(r => r.data?.relationships || r.data || []),
  });
}

export function useGraphStatistics() {
  return useQuery({
    queryKey: ['graph', 'statistics'],
    queryFn: () => graphApi.getStatistics().then(r => r.data),
    staleTime: 60_000,
  });
}

// ==================== Health ====================
export function useHealth() {
  return useQuery({
    queryKey: ['health'],
    queryFn: () => systemApi.health().then(r => r.data),
    staleTime: 30_000,
  });
}

// ==================== LLM Backends ====================
export interface LLMBackendRow {
  name: string;
  type: string;
  is_active: boolean;
  is_default: boolean;
  model?: string;
  base_url?: string;
  api_key_masked?: string;
  timeout?: number;
  created_at?: string;
  updated_at?: string;
  health_status?: 'healthy' | 'unhealthy' | 'unknown' | 'degraded';
}

export function useLLMBackends() {
  return useQuery({
    queryKey: ['llm-backends'],
    queryFn: () => systemApi.llmBackends().then(r => r.data?.backends || r.data || []),
  });
}

export function useRegisterLLMBackend() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { backend_name: string; backend_type: string; config: Record<string, unknown>; set_active?: boolean }) =>
      systemApi.registerLLMBackend(data),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['llm-backends'] }); },
  });
}

export function useDeleteLLMBackend() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (backendName: string) => systemApi.deleteLLMBackend(backendName),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['llm-backends'] }); },
  });
}

export function useSetDefaultLLMBackend() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (backendName: string) => systemApi.setDefaultLLMBackend(backendName),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['llm-backends'] }); },
  });
}

export function useCheckLLMBackendHealth(backendName?: string) {
  return useQuery({
    queryKey: ['llm-backends', backendName, 'health'],
    queryFn: () => systemApi.checkLLMBackendHealth(backendName!).then(r => r.data),
    enabled: !!backendName,
    staleTime: 10_000,
  });
}

// ==================== Variable Mutations ====================
export function useSetVariable() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { key: string; value: unknown; ttl?: number }) =>
      variablesApi.set(data.key, data.value, data.ttl),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['variables'] }); },
  });
}

export function useUpdateVariable() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { key: string; value: unknown }) =>
      variablesApi.update(data.key, data.value),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['variables'] }); },
  });
}

export function useDeleteVariable() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (key: string) => variablesApi.delete(key),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['variables'] }); },
  });
}

export function useUpdateVariableTtl() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { key: string; ttl: number | null }) =>
      variablesApi.updateTtl(data.key, data.ttl),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['variables'] }); },
  });
}

// ==================== Fragment Mutations ====================
export function useCreateFragment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { fragment_type?: string; content: string; importance_score?: number; ttl?: number }) =>
      fragmentsApi.create(data),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['fragments'] }); },
  });
}

export function useUpdateFragment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { id: number; content?: string; importance_score?: number; ttl?: number }) =>
      fragmentsApi.update(data.id, data),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['fragments'] }); },
  });
}

export function useDeleteFragment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => fragmentsApi.delete(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['fragments'] }); },
  });
}

export function useBatchDeleteFragments() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (ids: number[]) => fragmentsApi.batchDelete(ids),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['fragments'] }); },
  });
}

// ==================== Hybrid Search ====================
export function useHybridSearch() {
  return useMutation({
    mutationFn: (data: {
      query: string;
      alpha: number;
      beta: number;
      gamma: number;
      delta: number;
      top_k?: number;
    }) => hybridSearchApi.search(data).then(r => r.data),
  });
}

export function useHybridSearchConfig() {
  return useQuery({
    queryKey: ['hybrid-search', 'config'],
    queryFn: () => hybridSearchApi.config().then(r => r.data),
  });
}

export function useUpdateHybridSearchConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: Record<string, unknown>) => hybridSearchApi.updateConfig(data),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['hybrid-search', 'config'] }); },
  });
}

// ==================== Semantic Search ====================
export function useSemanticSearch() {
  return useMutation({
    mutationFn: (data: { query: string; topK?: number; threshold?: number }) =>
      fragmentsApi.semanticSearch(data.query, data.topK, data.threshold).then(r => r.data),
  });
}

// ==================== Table Mutations ====================
export function useCreateTable() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { table_name: string; fields: { name: string; type: string }[]; description?: string }) =>
      tablesApi.create(data),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['tables'] }); },
  });
}

export function useDropTable() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (tableName: string) => tablesApi.drop(tableName),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['tables'] }); },
  });
}

export function useDeleteRecord(tableName: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (recordId: number) => tablesApi.deleteRecord(tableName, recordId),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['tables', tableName, 'records'] }); },
  });
}

export function useBatchDeleteRecords(tableName: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (recordIds: number[]) => {
      const results = await Promise.allSettled(
        recordIds.map((id) => tablesApi.deleteRecord(tableName, id))
      );
      return results;
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['tables', tableName, 'records'] }); },
  });
}

export function useBatchImportRecords(tableName: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (records: Record<string, unknown>[]) => tablesApi.batchImport(tableName, records),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['tables', tableName, 'records'] }); },
  });
}

// ==================== Graph Mutations ====================
export function useCreateEntity() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { name: string; entity_type: string; properties?: Record<string, unknown> }) =>
      graphApi.createEntity(data),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['graph', 'entities'] }); },
  });
}

export function useCreateRelationship() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { source_entity_id: string; target_entity_id: string; relation_type: string; properties?: Record<string, unknown> }) =>
      graphApi.createRelationship(data),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['graph', 'relationships'] }); },
  });
}

// ==================== Recall ====================
export function useRecallConfig() {
  return useQuery({
    queryKey: ['recall', 'config'],
    queryFn: () => recallApi.config().then(r => r.data),
  });
}

export function useRecallStats() {
  return useQuery({
    queryKey: ['recall', 'stats'],
    queryFn: () => recallApi.stats().then(r => r.data),
  });
}

export function useUpdateRecallConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: Record<string, unknown>) => recallApi.updateConfig(data),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['recall'] }); },
  });
}

export const SESSION_PAGE_SIZE = 20;

// ==================== Sessions ====================
export function useSessionList(enabled = true) {
  return useQuery({
    queryKey: ['sessions'],
    queryFn: () => sessionsApi.list({ page: 1, page_size: 50 }).then(r => r.data?.sessions || []),
    enabled,
  });
}

export function useInfiniteSessionList() {
  return useInfiniteQuery({
    queryKey: ['sessions', 'infinite'],
    queryFn: ({ pageParam = 1 }) =>
      sessionsApi.list({ page: pageParam, page_size: SESSION_PAGE_SIZE }).then(r => r.data?.sessions || []),
    getNextPageParam: (lastPage, allPages) =>
      lastPage.length === SESSION_PAGE_SIZE ? allPages.length + 1 : undefined,
    initialPageParam: 1,
  });
}

export function useSearchSessions(q: string) {
  return useInfiniteQuery({
    queryKey: ['sessions', 'search', q],
    queryFn: ({ pageParam = 1 }) =>
      sessionsApi.search(q, { page: pageParam, page_size: SESSION_PAGE_SIZE }).then(r => r.data?.sessions || []),
    getNextPageParam: (lastPage, allPages) =>
      lastPage.length === SESSION_PAGE_SIZE ? allPages.length + 1 : undefined,
    initialPageParam: 1,
    enabled: q.trim().length > 0,
  });
}

export function useSessionMessages(sessionId?: string) {
  return useQuery({
    queryKey: ['sessions', sessionId, 'messages'],
    queryFn: () => sessionsApi.messages(sessionId!).then(r => r.data?.messages || []),
    enabled: !!sessionId,
  });
}

export function useDeleteSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (sessionId: string) => sessionsApi.delete(sessionId),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['sessions'] }); },
  });
}

export function useBatchDeleteSessions() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (sessionIds: string[]) => sessionsApi.batchDelete(sessionIds),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['sessions'] });
      qc.invalidateQueries({ queryKey: ['sessions', 'infinite'] });
    },
  });
}

export function useRenameSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { sessionId: string; title: string }) =>
      sessionsApi.rename(data.sessionId, data.title),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['sessions'] }); },
  });
}

// ==================== Session Summary ====================
export function useSessionSummary(sessionId?: string) {
  return useQuery({
    queryKey: ['sessions', sessionId, 'summary'],
    queryFn: () => sessionsApi.getSummary(sessionId!).then(r => r.data),
    enabled: !!sessionId,
    staleTime: 30_000,
  });
}

export function useSessionSummaryHistory(sessionId?: string) {
  return useQuery({
    queryKey: ['sessions', sessionId, 'summary', 'history'],
    queryFn: () => sessionsApi.getSummaryHistory(sessionId!).then(r => r.data),
    enabled: !!sessionId,
    staleTime: 30_000,
  });
}

export function useUpdateSessionSummary() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { sessionId: string; summary: string }) =>
      sessionsApi.updateSummary(data.sessionId, data.summary),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: ['sessions', variables.sessionId, 'summary'] });
    },
  });
}

export function useRegenerateSummary() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (sessionId: string) => sessionsApi.regenerateSummary(sessionId),
    onSuccess: (_data, sessionId) => {
      qc.invalidateQueries({ queryKey: ['sessions', sessionId, 'summary'] });
    },
  });
}

// ==================== Memory Lifecycle Conflicts ====================
export function useLifecycleConflicts() {
  return useQuery({
    queryKey: ['lifecycle', 'conflicts'],
    queryFn: () => lifecycleApi.listConflicts().then(r => r.data?.conflicts || r.data || []),
  });
}

export function useLifecycleMergeLog() {
  return useQuery({
    queryKey: ['lifecycle', 'merge-log'],
    queryFn: () => lifecycleApi.mergeLog().then(r => r.data?.logs || r.data || []),
  });
}

export function useDetectConflict() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { key: string; new_value: string }) =>
      lifecycleApi.detectConflicts(data.key, data.new_value),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['lifecycle', 'conflicts'] }); },
  });
}

export function useResolveConflict() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { conflictId: number; resolution: string; mergedValue?: string }) =>
      lifecycleApi.resolveConflict(data.conflictId, data.resolution, data.mergedValue),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['lifecycle', 'conflicts'] });
      qc.invalidateQueries({ queryKey: ['lifecycle', 'merge-log'] });
      qc.invalidateQueries({ queryKey: ['variables'] });
    },
  });
}
