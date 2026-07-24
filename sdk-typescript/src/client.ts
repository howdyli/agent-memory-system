/**
 * Agent Memory SDK — TypeScript client.
 */

import { Transport, type TransportOptions } from './transport.js';
import type {
  MemoryClientOptions,
  MemoryVariable,
  MemoryFragment,
  MemoryTable,
  TableField,
  GraphEntity,
  GraphRelationship,
  RecallResult,
  Workspace,
  WorkspaceMember,
  ApiKey,
  ApiKeyCreated,
  Session,
} from './types.js';

// ==================== API Submodules ====================

export class VariablesAPI {
  constructor(private t: Transport) {}

  async set(key: string, value: unknown, ttl?: number): Promise<boolean> {
    const r = await this.t.request<{ success?: boolean }>('POST', '/memory/variables', {
      json: { key, value, ttl },
    });
    return r?.success ?? true;
  }

  async get(key: string): Promise<MemoryVariable | null> {
    return this.t.request<MemoryVariable>('GET', `/memory/variables/${encodeURIComponent(key)}`);
  }

  async delete(key: string): Promise<boolean> {
    const r = await this.t.request<{ success?: boolean }>('DELETE', `/memory/variables/${encodeURIComponent(key)}`);
    return r?.success ?? true;
  }

  async list(sessionId?: string): Promise<Record<string, unknown>> {
    return this.t.request('GET', '/memory/variables', {
      params: sessionId ? { session_id: sessionId, detailed: true } : { detailed: true },
    });
  }

  async update(key: string, value: unknown): Promise<boolean> {
    const r = await this.t.request<{ success?: boolean }>('PUT', `/memory/variables/${encodeURIComponent(key)}`, {
      json: { value },
    });
    return r?.success ?? true;
  }
}

export class FragmentsAPI {
  constructor(private t: Transport) {}

  async create(data: {
    content: string;
    fragment_type?: string;
    importance_score?: number;
    ttl?: number;
  }): Promise<Record<string, unknown>> {
    return this.t.request('POST', '/memory/fragments', {
      json: {
        fragment_type: data.fragment_type ?? 'fact',
        content: data.content,
        importance_score: data.importance_score ?? 0.5,
        ttl: data.ttl,
      },
    });
  }

  async get(id: number): Promise<MemoryFragment> {
    return this.t.request('GET', `/memory/fragments/${id}`);
  }

  async delete(id: number): Promise<boolean> {
    const r = await this.t.request<{ success?: boolean }>('DELETE', `/memory/fragments/${id}`);
    return r?.success ?? true;
  }

  async list(type?: string): Promise<MemoryFragment[]> {
    const r = await this.t.request<{ fragments?: MemoryFragment[] }>('GET', '/memory/fragments', {
      params: type ? { type } : {},
    });
    return r?.fragments ?? [];
  }

  async search(query: string, topK = 5, threshold = 0.3): Promise<MemoryFragment[]> {
    const r = await this.t.request<{ results?: MemoryFragment[]; fragments?: MemoryFragment[] }>(
      'POST', '/memory/fragments/search',
      { json: { query, top_k: topK, threshold } },
    );
    return r?.results ?? r?.fragments ?? [];
  }
}

export class TablesAPI {
  constructor(private t: Transport) {}

  async list(): Promise<MemoryTable[]> {
    const r = await this.t.request<{ tables?: MemoryTable[] }>('GET', '/memory/tables/');
    return r?.tables ?? [];
  }

  async create(tableName: string, fields: TableField[], description?: string): Promise<Record<string, unknown>> {
    return this.t.request('POST', '/memory/tables/', {
      json: { table_name: tableName, fields, description },
    });
  }

  async info(tableName: string): Promise<Record<string, unknown>> {
    return this.t.request('GET', `/memory/tables/${encodeURIComponent(tableName)}/info`);
  }

  async drop(tableName: string): Promise<Record<string, unknown>> {
    return this.t.request('DELETE', `/memory/tables/${encodeURIComponent(tableName)}`);
  }

  async addRecord(tableName: string, record: Record<string, unknown>): Promise<Record<string, unknown>> {
    return this.t.request('POST', `/memory/tables/${encodeURIComponent(tableName)}/records`, {
      json: { record },
    });
  }

  async queryRecords(tableName: string): Promise<Record<string, unknown>[]> {
    const r = await this.t.request<{ records?: Record<string, unknown>[] }>('GET', `/memory/tables/${encodeURIComponent(tableName)}/records`);
    return r?.records ?? [];
  }
}

export class GraphAPI {
  constructor(private t: Transport) {}

  async searchEntities(query?: string): Promise<GraphEntity[]> {
    const r = await this.t.request<{ entities?: GraphEntity[] }>('GET', '/memory/graph/entities', {
      params: query ? { query } : {},
    });
    return r?.entities ?? [];
  }

  async getEntity(id: string): Promise<GraphEntity> {
    return this.t.request('GET', `/memory/graph/entities/${encodeURIComponent(id)}`);
  }

  async createEntity(name: string, entityType: string, properties?: Record<string, unknown>): Promise<Record<string, unknown>> {
    return this.t.request('POST', '/memory/graph/entities', {
      json: { name, entity_type: entityType, properties },
    });
  }

  async deleteEntity(id: string): Promise<Record<string, unknown>> {
    return this.t.request('DELETE', `/memory/graph/entities/${encodeURIComponent(id)}`);
  }

  async getNeighbors(entityId: string, depth = 1): Promise<Record<string, unknown>> {
    return this.t.request('GET', '/memory/graph/neighbors', {
      params: { entity_id: entityId, depth },
    });
  }

  async queryGraph(query: string): Promise<Record<string, unknown>> {
    return this.t.request('GET', '/memory/graph/query', { params: { q: query } });
  }

  async extractEntities(text: string): Promise<Record<string, unknown>> {
    return this.t.request('POST', '/memory/graph/extract', { json: { text } });
  }

  async getStatistics(): Promise<Record<string, unknown>> {
    return this.t.request('GET', '/memory/graph/statistics');
  }
}

export class RecallAPI {
  constructor(private t: Transport) {}

  async auto(query: string, topK?: number): Promise<RecallResult> {
    return this.t.request('POST', '/memory/recall', { json: { query, top_k: topK } });
  }

  async search(query: string, topK = 5): Promise<Record<string, unknown>[]> {
    const r = await this.t.request<{ memories?: Record<string, unknown>[] }>('POST', '/memory/recall/search', {
      json: { query, top_k: topK },
    });
    return r?.memories ?? [];
  }

  async config(): Promise<Record<string, unknown>> {
    return this.t.request('GET', '/memory/recall/config');
  }

  async stats(): Promise<Record<string, unknown>> {
    return this.t.request('GET', '/memory/recall/stats');
  }
}

export class WorkspaceAPI {
  constructor(private t: Transport) {}

  async list(): Promise<Workspace[]> {
    return this.t.request('GET', '/workspaces');
  }

  async create(data: { name: string; slug: string; kind?: string }): Promise<Workspace> {
    return this.t.request('POST', '/workspaces', { json: data });
  }

  async get(id: number): Promise<Workspace> {
    return this.t.request('GET', `/workspaces/${id}`);
  }

  async addMember(id: number, userId: number, role?: string): Promise<WorkspaceMember> {
    return this.t.request('POST', `/workspaces/${id}/members`, { json: { user_id: userId, role } });
  }

  async removeMember(id: number, userId: number): Promise<void> {
    await this.t.request('DELETE', `/workspaces/${id}/members/${userId}`);
  }
}

export class ApiKeyAPI {
  constructor(private t: Transport) {}

  async list(): Promise<ApiKey[]> {
    return this.t.request('GET', '/auth/api-keys');
  }

  async create(data: { name: string; scopes?: string[]; expires_at?: string }): Promise<ApiKeyCreated> {
    return this.t.request('POST', '/auth/api-keys', { json: data });
  }

  async revoke(keyId: number): Promise<void> {
    await this.t.request('DELETE', `/auth/api-keys/${keyId}`);
  }
}

// ==================== Events & Webhooks (Phase 4) ====================

export interface MemoryEvent {
  event_id: string;
  event_type: string;
  user_id: number;
  workspace_id?: number;
  memory_id: string;
  memory_type: string;
  timestamp: string;
  data: Record<string, unknown>;
  source: string;
}

export interface Webhook {
  id: number;
  user_id: number;
  url: string;
  secret: string;
  event_types: string[];
  workspace_id?: number;
  description?: string;
  active: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface WebhookDelivery {
  id: number;
  webhook_id: number;
  event_id: string;
  event_type: string;
  status_code?: number;
  success: boolean;
  attempt: number;
  next_retry_at?: string;
  created_at?: string;
}

export class EventsAPI {
  constructor(private t: Transport) {}

  async list(opts?: { event_type?: string; days?: number; limit?: number }): Promise<MemoryEvent[]> {
    const params: Record<string, unknown> = {};
    if (opts?.event_type) params.event_type = opts.event_type;
    if (opts?.days) params.days = opts.days;
    if (opts?.limit) params.limit = opts.limit;
    return this.t.request<MemoryEvent[]>('GET', '/events', { params });
  }

  async listEventTypes(): Promise<string[]> {
    const r = await this.t.request<{ event_types: string[] }>('GET', '/events/types');
    return r?.event_types ?? [];
  }
}

export class WebhooksAPI {
  constructor(private t: Transport) {}

  async list(workspaceId?: number): Promise<Webhook[]> {
    const params: Record<string, unknown> = {};
    if (workspaceId) params.workspace_id = workspaceId;
    return this.t.request<Webhook[]>('GET', '/webhooks', { params });
  }

  async create(data: { url: string; event_types: string[]; workspace_id?: number; description?: string }): Promise<Webhook> {
    return this.t.request<Webhook>('POST', '/webhooks', { json: data });
  }

  async get(id: number): Promise<Webhook> {
    return this.t.request<Webhook>('GET', `/webhooks/${id}`);
  }

  async update(id: number, data: { url?: string; event_types?: string[]; active?: boolean; description?: string }): Promise<Webhook> {
    return this.t.request<Webhook>('PUT', `/webhooks/${id}`, { json: data });
  }

  async delete(id: number): Promise<boolean> {
    const r = await this.t.request<{ success?: boolean }>('DELETE', `/webhooks/${id}`);
    return r?.success ?? true;
  }

  async test(id: number): Promise<{ success: boolean; status_code?: number; response?: string }> {
    return this.t.request('POST', `/webhooks/${id}/test`);
  }

  async deliveries(id: number, limit = 50): Promise<WebhookDelivery[]> {
    return this.t.request<WebhookDelivery[]>('GET', `/webhooks/${id}/deliveries`, { params: { limit } });
  }
}

// ==================== MemoryClient ====================

export class MemoryClient {
  private transport: Transport;

  public readonly variables: VariablesAPI;
  public readonly fragments: FragmentsAPI;
  public readonly tables: TablesAPI;
  public readonly graph: GraphAPI;
  public readonly recall: RecallAPI;
  public readonly workspaces: WorkspaceAPI;
  public readonly apiKeys: ApiKeyAPI;
  public readonly events: EventsAPI;
  public readonly webhooks: WebhooksAPI;

  constructor(opts: MemoryClientOptions) {
    this.transport = new Transport({
      baseUrl: opts.baseUrl,
      apiKey: opts.apiKey,
      token: opts.token,
      workspaceId: opts.workspaceId,
      timeout: opts.timeout,
    });

    this.variables = new VariablesAPI(this.transport);
    this.fragments = new FragmentsAPI(this.transport);
    this.tables = new TablesAPI(this.transport);
    this.graph = new GraphAPI(this.transport);
    this.recall = new RecallAPI(this.transport);
    this.workspaces = new WorkspaceAPI(this.transport);
    this.apiKeys = new ApiKeyAPI(this.transport);
    this.events = new EventsAPI(this.transport);
    this.webhooks = new WebhooksAPI(this.transport);
  }

  // High-level convenience methods

  async remember(key: string, value: unknown, ttl?: number): Promise<boolean> {
    return this.variables.set(key, value, ttl);
  }

  async recallContext(query: string, topK = 5): Promise<string> {
    try {
      const result = await this.recall.auto(query);
      return result?.context ?? '';
    } catch {
      return '';
    }
  }

  async forget(key: string): Promise<boolean> {
    return this.variables.delete(key);
  }

  async search(query: string, topK = 5): Promise<MemoryFragment[]> {
    return this.fragments.search(query, topK);
  }
}
