import { test, describe, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { Transport } from './transport.js';
import {
  HTTPError,
  AuthenticationError,
  PermissionDeniedError,
  NotFoundError,
  TransportError,
} from './errors.js';

// 捕获 fetch 调用参数的全局 mock
let fetchCalls: { url: string; init: RequestInit }[] = [];
let originalFetch: typeof globalThis.fetch;

function mockFetch(responses: Array<{ status: number; body?: unknown; contentType?: string }>) {
  let callIndex = 0;
  originalFetch = globalThis.fetch;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString();
    fetchCalls.push({ url, init: init ?? {} });
    const resp = responses[Math.min(callIndex, responses.length - 1)];
    callIndex++;
    const headers = new Map([['content-type', resp.contentType ?? 'application/json']]);
    return {
      ok: resp.status >= 200 && resp.status < 300,
      status: resp.status,
      headers: { get: (name: string) => headers.get(name.toLowerCase()) ?? null },
      json: async () => resp.body,
      text: async () => (typeof resp.body === 'string' ? resp.body : JSON.stringify(resp.body)),
    } as unknown as Response;
  }) as typeof globalThis.fetch;
}

describe('Transport', () => {
  beforeEach(() => {
    fetchCalls = [];
  });
  afterEach(() => {
    if (originalFetch) {
      globalThis.fetch = originalFetch;
      originalFetch = undefined as never;
    }
  });

  test('strips trailing slashes from baseUrl', () => {
    const t = new Transport({ baseUrl: 'http://localhost:8000///' });
    // 内部 baseUrl 已去除尾部斜杠，通过请求 URL 验证
    mockFetch([{ status: 200, body: { ok: true } }]);
    t.request('GET', '/test');
    assert.equal(fetchCalls[0].url, 'http://localhost:8000/test');
  });

  test('sets Authorization header from apiKey', async () => {
    const t = new Transport({ baseUrl: 'http://localhost:8000', apiKey: 'sk-test123' });
    mockFetch([{ status: 200, body: { ok: true } }]);
    await t.request('GET', '/test');
    const headers = new Headers(fetchCalls[0].init.headers as HeadersInit);
    assert.equal(headers.get('Authorization'), 'Bearer sk-test123');
  });

  test('sets Authorization header from token', async () => {
    const t = new Transport({ baseUrl: 'http://localhost:8000', token: 'jwt-token' });
    mockFetch([{ status: 200, body: { ok: true } }]);
    await t.request('GET', '/test');
    const headers = new Headers(fetchCalls[0].init.headers as HeadersInit);
    assert.equal(headers.get('Authorization'), 'Bearer jwt-token');
  });

  test('sets X-Workspace-Id header', async () => {
    const t = new Transport({ baseUrl: 'http://localhost:8000', workspaceId: 42 });
    mockFetch([{ status: 200, body: { ok: true } }]);
    await t.request('GET', '/test');
    const headers = new Headers(fetchCalls[0].init.headers as HeadersInit);
    assert.equal(headers.get('X-Workspace-Id'), '42');
  });

  test('appends query params', async () => {
    const t = new Transport({ baseUrl: 'http://localhost:8000' });
    mockFetch([{ status: 200, body: { ok: true } }]);
    await t.request('GET', '/test', { params: { foo: 'bar', num: 123, skip: null } });
    assert.match(fetchCalls[0].url, /foo=bar/);
    assert.match(fetchCalls[0].url, /num=123/);
    assert.doesNotMatch(fetchCalls[0].url, /skip/);
  });

  test('sends JSON body for POST', async () => {
    const t = new Transport({ baseUrl: 'http://localhost:8000' });
    mockFetch([{ status: 201, body: { id: 1 } }]);
    await t.request('POST', '/items', { json: { name: 'test' } });
    assert.equal(fetchCalls[0].init.method, 'POST');
    assert.equal(fetchCalls[0].init.body, JSON.stringify({ name: 'test' }));
  });

  test('maps 401 to AuthenticationError', async () => {
    const t = new Transport({ baseUrl: 'http://localhost:8000' });
    mockFetch([{ status: 401, body: 'Unauthorized' }]);
    await assert.rejects(() => t.request('GET', '/test'), (err: unknown) => {
      assert.ok(err instanceof AuthenticationError);
      assert.equal((err as AuthenticationError).statusCode, 401);
      return true;
    });
  });

  test('maps 403 to PermissionDeniedError', async () => {
    const t = new Transport({ baseUrl: 'http://localhost:8000' });
    mockFetch([{ status: 403, body: 'Forbidden' }]);
    await assert.rejects(() => t.request('GET', '/test'), (err: unknown) => {
      assert.ok(err instanceof PermissionDeniedError);
      return true;
    });
  });

  test('maps 404 to NotFoundError', async () => {
    const t = new Transport({ baseUrl: 'http://localhost:8000' });
    mockFetch([{ status: 404, body: 'Not Found' }]);
    await assert.rejects(() => t.request('GET', '/test'), (err: unknown) => {
      assert.ok(err instanceof NotFoundError);
      return true;
    });
  });

  test('maps 500 to HTTPError', async () => {
    const t = new Transport({ baseUrl: 'http://localhost:8000' });
    mockFetch([{ status: 500, body: 'Internal Server Error' }]);
    await assert.rejects(() => t.request('GET', '/test'), (err: unknown) => {
      assert.ok(err instanceof HTTPError);
      assert.ok(!(err instanceof AuthenticationError));
      assert.equal((err as HTTPError).statusCode, 500);
      return true;
    });
  });

  test('returns undefined for 204', async () => {
    const t = new Transport({ baseUrl: 'http://localhost:8000' });
    mockFetch([{ status: 204, body: undefined }]);
    const result = await t.request('DELETE', '/test');
    assert.equal(result, undefined);
  });

  test('wraps network errors in TransportError', async () => {
    const t = new Transport({ baseUrl: 'http://localhost:8000' });
    originalFetch = globalThis.fetch;
    globalThis.fetch = (async () => {
      throw new TypeError('fetch failed');
    }) as typeof globalThis.fetch;
    await assert.rejects(() => t.request('GET', '/test'), (err: unknown) => {
      assert.ok(err instanceof TransportError);
      assert.match((err as Error).message, /fetch failed/);
      return true;
    });
  });
});
