import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { MemoryClient } from './client.js';
import { Transport } from './transport.js';
import type { TransportOptions } from './transport.js';

// Mock Transport: 记录调用并返回预设响应
class MockTransport extends Transport {
  public calls: { method: string; path: string; options?: { json?: unknown; params?: Record<string, unknown> } }[] = [];
  private response: unknown;

  constructor(response: unknown = { success: true }) {
    super({ baseUrl: 'http://mock' } as TransportOptions);
    this.response = response;
  }

  override async request<T = unknown>(
    method: string,
    path: string,
    options?: { json?: unknown; params?: Record<string, unknown> },
  ): Promise<T> {
    this.calls.push({ method, path, options });
    return this.response as T;
  }
}

describe('VariablesAPI', () => {
  test('set sends POST with key, value, ttl', async () => {
    const t = new MockTransport({ success: true });
    const client = new MemoryClient({ baseUrl: 'http://mock' });
    // 替换内部 transport
    (client as unknown as { transport: Transport }).transport = t;
    (client as unknown as { variables: { t: Transport } }).variables.t = t;

    const result = await client.variables.set('foo', { count: 1 }, 60);
    assert.equal(result, true);
    assert.equal(t.calls[0].method, 'POST');
    assert.equal(t.calls[0].path, '/memory/variables');
    assert.deepEqual(t.calls[0].options?.json, { key: 'foo', value: { count: 1 }, ttl: 60 });
  });

  test('get returns variable', async () => {
    const mockVar = { key: 'foo', value: 'bar' };
    const t = new MockTransport(mockVar);
    const client = new MemoryClient({ baseUrl: 'http://mock' });
    (client as unknown as { variables: { t: Transport } }).variables.t = t;

    const result = await client.variables.get('foo');
    assert.equal(result, mockVar);
    assert.equal(t.calls[0].path, '/memory/variables/foo');
  });

  test('delete returns true', async () => {
    const t = new MockTransport({ success: true });
    const client = new MemoryClient({ baseUrl: 'http://mock' });
    (client as unknown as { variables: { t: Transport } }).variables.t = t;

    const result = await client.variables.delete('foo');
    assert.equal(result, true);
    assert.equal(t.calls[0].method, 'DELETE');
  });
});

describe('FragmentsAPI', () => {
  test('create sends POST with defaults', async () => {
    const t = new MockTransport({ id: 1 });
    const client = new MemoryClient({ baseUrl: 'http://mock' });
    (client as unknown as { fragments: { t: Transport } }).fragments.t = t;

    await client.fragments.create({ content: 'hello world' });
    assert.deepStrictEqual(t.calls[0].options?.json, {
      fragment_type: 'fact',
      content: 'hello world',
      importance_score: 0.5,
      ttl: undefined,
    });
  });

  test('search returns results array', async () => {
    const t = new MockTransport({ results: [{ id: 1 }, { id: 2 }] });
    const client = new MemoryClient({ baseUrl: 'http://mock' });
    (client as unknown as { fragments: { t: Transport } }).fragments.t = t;

    const results = await client.fragments.search('query', 10, 0.5);
    assert.equal(results.length, 2);
    assert.deepStrictEqual(t.calls[0].options?.json, { query: 'query', top_k: 10, threshold: 0.5 });
  });

  test('list returns fragments array', async () => {
    const t = new MockTransport({ fragments: [{ id: 1 }] });
    const client = new MemoryClient({ baseUrl: 'http://mock' });
    (client as unknown as { fragments: { t: Transport } }).fragments.t = t;

    const results = await client.fragments.list('fact');
    assert.equal(results.length, 1);
    assert.deepStrictEqual(t.calls[0].options?.params, { type: 'fact' });
  });
});

describe('RecallAPI', () => {
  test('auto sends POST with query and top_k', async () => {
    const t = new MockTransport({ context: 'result' });
    const client = new MemoryClient({ baseUrl: 'http://mock' });
    (client as unknown as { recall: { t: Transport } }).recall.t = t;

    await client.recall.auto('question', 5);
    assert.equal(t.calls[0].method, 'POST');
    assert.equal(t.calls[0].path, '/memory/recall');
    assert.deepStrictEqual(t.calls[0].options?.json, { query: 'question', top_k: 5 });
  });
});

describe('MemoryClient convenience methods', () => {
  test('remember delegates to variables.set', async () => {
    const t = new MockTransport({ success: true });
    const client = new MemoryClient({ baseUrl: 'http://mock' });
    (client as unknown as { variables: { t: Transport } }).variables.t = t;

    const result = await client.remember('key', 'value');
    assert.equal(result, true);
    assert.equal(t.calls[0].path, '/memory/variables');
  });

  test('forget delegates to variables.delete', async () => {
    const t = new MockTransport({ success: true });
    const client = new MemoryClient({ baseUrl: 'http://mock' });
    (client as unknown as { variables: { t: Transport } }).variables.t = t;

    const result = await client.forget('key');
    assert.equal(result, true);
    assert.equal(t.calls[0].method, 'DELETE');
  });

  test('recallContext returns context string on success', async () => {
    const t = new MockTransport({ context: 'recalled context' });
    const client = new MemoryClient({ baseUrl: 'http://mock' });
    (client as unknown as { recall: { t: Transport } }).recall.t = t;

    const ctx = await client.recallContext('query');
    assert.equal(ctx, 'recalled context');
  });

  test('recallContext returns empty string on error', async () => {
    const client = new MemoryClient({ baseUrl: 'http://invalid-host-99999' });
    // fetch 会失败，recallContext 应捕获异常返回空字符串
    const ctx = await client.recallContext('query');
    assert.equal(ctx, '');
  });
});

describe('WebhooksAPI', () => {
  test('create sends POST with url and event_types', async () => {
    const t = new MockTransport({ id: 1, url: 'http://hook' });
    const client = new MemoryClient({ baseUrl: 'http://mock' });
    (client as unknown as { webhooks: { t: Transport } }).webhooks.t = t;

    await client.webhooks.create({ url: 'http://hook', event_types: ['memory.created'] });
    assert.equal(t.calls[0].method, 'POST');
    assert.equal(t.calls[0].path, '/webhooks');
    assert.deepStrictEqual(t.calls[0].options?.json, { url: 'http://hook', event_types: ['memory.created'] });
  });

  test('delete returns true on success response', async () => {
    const t = new MockTransport({ success: true });
    const client = new MemoryClient({ baseUrl: 'http://mock' });
    (client as unknown as { webhooks: { t: Transport } }).webhooks.t = t;

    const result = await client.webhooks.delete(1);
    assert.equal(result, true);
  });
});
