/**
 * HTTP Transport layer for Agent Memory SDK.
 */

import {
  HTTPError,
  AuthenticationError,
  PermissionDeniedError,
  NotFoundError,
  TransportError,
} from './errors.js';

export interface TransportOptions {
  baseUrl: string;
  apiKey?: string;
  token?: string;
  workspaceId?: string | number;
  timeout?: number;
}

export class Transport {
  private baseUrl: string;
  private headers: Record<string, string>;
  private timeout: number;

  constructor(opts: TransportOptions) {
    this.baseUrl = opts.baseUrl.replace(/\/+$/, '');
    this.timeout = opts.timeout ?? 30000;

    this.headers = { 'Content-Type': 'application/json' };
    if (opts.apiKey) {
      this.headers['Authorization'] = `Bearer ${opts.apiKey}`;
    } else if (opts.token) {
      this.headers['Authorization'] = `Bearer ${opts.token}`;
    }
    if (opts.workspaceId) {
      this.headers['X-Workspace-Id'] = String(opts.workspaceId);
    }
  }

  async request<T = unknown>(
    method: string,
    path: string,
    options?: { json?: unknown; params?: Record<string, unknown> },
  ): Promise<T> {
    let url = `${this.baseUrl}${path}`;

    if (options?.params) {
      const searchParams = new URLSearchParams();
      for (const [k, v] of Object.entries(options.params)) {
        if (v !== undefined && v !== null) {
          searchParams.set(k, String(v));
        }
      }
      const qs = searchParams.toString();
      if (qs) url += `?${qs}`;
    }

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeout);

    try {
      const response = await fetch(url, {
        method: method.toUpperCase(),
        headers: this.headers,
        body: options?.json ? JSON.stringify(options.json) : undefined,
        signal: controller.signal,
      });

      return await this.handleResponse<T>(response);
    } catch (err) {
      if (err instanceof HTTPError) throw err;
      throw new TransportError(`Request failed: ${(err as Error).message}`);
    } finally {
      clearTimeout(timer);
    }
  }

  /**
   * SSE stream request.
   */
  async requestStream(
    method: string,
    path: string,
    json?: unknown,
  ): Promise<AsyncIterable<string>> {
    const url = `${this.baseUrl}${path}`;
    const response = await fetch(url, {
      method: method.toUpperCase(),
      headers: this.headers,
      body: json ? JSON.stringify(json) : undefined,
    });

    if (!response.ok) {
      const text = await response.text();
      this.raiseForStatus(response.status, text);
    }

    const self = this;
    return {
      async *[Symbol.asyncIterator]() {
        const reader = response.body?.getReader();
        if (!reader) return;
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() ?? '';
          for (const line of lines) {
            if (line.trim()) yield line;
          }
        }
        if (buffer.trim()) yield buffer;
      },
    };
  }

  private async handleResponse<T>(response: Response): Promise<T> {
    if (response.status === 204) return undefined as T;

    if (response.ok) {
      const contentType = response.headers.get('content-type') ?? '';
      if (contentType.includes('application/json')) {
        return (await response.json()) as T;
      }
      return (await response.text()) as T;
    }

    const text = await response.text();
    this.raiseForStatus(response.status, text);
    throw new HTTPError(response.status, text);
  }

  private raiseForStatus(status: number, body: string): never {
    const detail = body.slice(0, 500);
    if (status === 401) throw new AuthenticationError(detail);
    if (status === 403) throw new PermissionDeniedError(detail);
    if (status === 404) throw new NotFoundError(detail);
    throw new HTTPError(status, detail);
  }
}
