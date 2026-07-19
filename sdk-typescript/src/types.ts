/**
 * Business types for Agent Memory SDK.
 */

// ==================== Auth ====================
export interface AuthUser {
  user_id: number;
  username: string;
  email?: string;
}

// ==================== Workspace ====================
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

// ==================== API Key ====================
export interface ApiKey {
  id: number;
  name: string;
  key_prefix: string;
  scopes: string;
  last_used_at?: string;
  expires_at?: string;
  created_at: string;
}

export interface ApiKeyCreated {
  id: number;
  key: string;
  name: string;
  expires_at?: string;
}

// ==================== Memory Variable ====================
export interface MemoryVariable {
  key: string;
  value: unknown;
  ttl?: number | null;
  session_id?: string | null;
  created_at?: string;
  updated_at?: string;
  expires_at?: string | null;
}

// ==================== Memory Fragment ====================
export interface MemoryFragment {
  id?: number;
  content: string;
  fragment_type: string;
  importance_score: number;
  ttl?: number | null;
  session_id?: string | null;
  created_at?: string;
  updated_at?: string;
  similarity_score?: number;
  distance?: number;
}

// ==================== Memory Table ====================
export interface TableField {
  name: string;
  type: string;
  nullable?: boolean;
  default?: unknown;
}

export interface MemoryTable {
  name: string;
  fields: TableField[];
  description?: string;
  record_count?: number;
  created_at?: string;
}

export interface TableRecord {
  id?: number;
  [key: string]: unknown;
}

// ==================== Graph ====================
export interface GraphEntity {
  id?: string;
  name: string;
  entity_type: string;
  properties?: Record<string, unknown>;
  aliases?: string[];
  created_at?: string;
  updated_at?: string;
}

export interface GraphRelationship {
  id?: string;
  source_entity_id?: string;
  target_entity_id?: string;
  source_name?: string;
  target_name?: string;
  relation_type: string;
  properties?: Record<string, unknown>;
  confidence?: number;
  created_at?: string;
}

// ==================== Recall ====================
export interface RecallResult {
  success: boolean;
  context: string;
  memories: Record<string, unknown>[];
  query?: string;
  total_count: number;
}

// ==================== Session ====================
export interface Session {
  id: number;
  session_id: string;
  user_id: number;
  title: string;
  message_count: number;
  created_at: string;
  updated_at: string;
  highlights?: string[];
}

// ==================== SDK Options ====================
export interface MemoryClientOptions {
  baseUrl: string;
  apiKey?: string;
  token?: string;
  workspaceId?: string | number;
  timeout?: number;
}
