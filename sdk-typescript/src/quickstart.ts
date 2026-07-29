/**
 * Zero-config quickstart factory (W3-F3.4) — 对齐 Python SDK。
 *
 * ```typescript
 * import { quickstart } from '@agent-memory/sdk';
 *
 * const mem = quickstart(); // 自动检测 AGENT_MEMORY_URL 环境变量
 * await mem.remember('user_name', '鑫海');
 * const ctx = await mem.recallContext('鑫海的信息');
 * ```
 */

import { MemoryClient } from './client.js';
import type { MemoryClientOptions } from './types.js';

// 环境变量名（与 Python SDK 一致）
export const ENV_URL = 'AGENT_MEMORY_URL';
export const ENV_API_KEY = 'AGENT_MEMORY_API_KEY';

// 未检测到环境变量时的本地开发默认地址
export const DEFAULT_BASE_URL = 'http://localhost:8000/api/v1';

export type PresetName = 'chatbot' | 'knowledge' | 'assistant';

export interface PresetConfig {
  recallTopK: number;
  semanticThreshold: number;
  preferenceHalfLifeDays: number;
  planHalfLifeDays: number;
}

/** 预设配置模板（与 Python SDK presets.py 保持一致） */
export const PRESETS: Record<PresetName, PresetConfig> = {
  chatbot: {
    recallTopK: 5,
    semanticThreshold: 0.4,
    preferenceHalfLifeDays: 1,
    planHalfLifeDays: 30,
  },
  knowledge: {
    recallTopK: 10,
    semanticThreshold: 0.2,
    preferenceHalfLifeDays: 30,
    planHalfLifeDays: 180,
  },
  assistant: {
    recallTopK: 5,
    semanticThreshold: 0.3,
    preferenceHalfLifeDays: 1,
    planHalfLifeDays: 90,
  },
};

export interface QuickstartOptions extends Partial<MemoryClientOptions> {
  preset?: PresetName;
}

/** 读取环境变量（Node 环境；浏览器等无 process 环境返回 undefined） */
function readEnv(name: string): string | undefined {
  if (typeof process !== 'undefined' && process.env) {
    const v = process.env[name]?.trim();
    return v || undefined;
  }
  return undefined;
}

/**
 * 零配置创建可用的 MemoryClient。
 *
 * 自动检测 AGENT_MEMORY_URL / AGENT_MEMORY_API_KEY 环境变量；
 * 未检测到时回退本地开发地址 http://localhost:8000/api/v1。
 * 显式传入的 opts 优先于环境变量。
 */
export function quickstart(opts: QuickstartOptions = {}): MemoryClient {
  const preset = PRESETS[opts.preset ?? 'assistant'];
  if (!preset) {
    throw new Error(
      `未知预设: ${opts.preset}，可用预设: ${Object.keys(PRESETS).join(', ')}`,
    );
  }

  const baseUrl = opts.baseUrl ?? readEnv(ENV_URL) ?? DEFAULT_BASE_URL;
  const apiKey = opts.apiKey ?? readEnv(ENV_API_KEY);

  const client = new MemoryClient({
    baseUrl,
    apiKey,
    token: opts.token,
    workspaceId: opts.workspaceId,
    timeout: opts.timeout,
  });
  client.configure(preset);
  return client;
}
