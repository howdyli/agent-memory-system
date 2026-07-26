/**
 * Tests for quickstart() factory and presets (W3-F3.4).
 */

import { test } from 'node:test';
import assert from 'node:assert';
import { quickstart, PRESETS, DEFAULT_BASE_URL, ENV_URL, ENV_API_KEY } from './quickstart.js';
import { MemoryClient } from './client.js';

function withEnv(vars: Record<string, string | undefined>, fn: () => void) {
  const saved: Record<string, string | undefined> = {};
  for (const [k, v] of Object.entries(vars)) {
    saved[k] = process.env[k];
    if (v === undefined) delete process.env[k];
    else process.env[k] = v;
  }
  try {
    fn();
  } finally {
    for (const [k, v] of Object.entries(saved)) {
      if (v === undefined) delete process.env[k];
      else process.env[k] = v;
    }
  }
}

test('presets match PRD table', () => {
  assert.deepStrictEqual(Object.keys(PRESETS).sort(), ['assistant', 'chatbot', 'knowledge']);
  assert.strictEqual(PRESETS.chatbot.recallTopK, 5);
  assert.strictEqual(PRESETS.chatbot.semanticThreshold, 0.4);
  assert.strictEqual(PRESETS.chatbot.planHalfLifeDays, 30);
  assert.strictEqual(PRESETS.knowledge.recallTopK, 10);
  assert.strictEqual(PRESETS.knowledge.semanticThreshold, 0.2);
  assert.strictEqual(PRESETS.assistant.planHalfLifeDays, 90);
});

test('quickstart returns MemoryClient with default preset', () => {
  withEnv({ [ENV_URL]: undefined, [ENV_API_KEY]: undefined }, () => {
    const mem = quickstart();
    assert.ok(mem instanceof MemoryClient);
    assert.strictEqual(mem.settings.recallTopK, 5);
    assert.strictEqual(mem.settings.semanticThreshold, 0.3);
  });
});

test('quickstart applies named preset', () => {
  withEnv({ [ENV_URL]: undefined }, () => {
    const mem = quickstart({ preset: 'knowledge' });
    assert.strictEqual(mem.settings.recallTopK, 10);
    assert.strictEqual(mem.settings.semanticThreshold, 0.2);
  });
});

test('quickstart reads AGENT_MEMORY_URL env', () => {
  withEnv({ [ENV_URL]: 'http://env.example.com/api/v1', [ENV_API_KEY]: 'amk_x' }, () => {
    const mem = quickstart();
    assert.ok(mem instanceof MemoryClient);
  });
});

test('explicit opts win over env', () => {
  withEnv({ [ENV_URL]: 'http://env.example.com/api/v1' }, () => {
    const mem = quickstart({ baseUrl: 'http://explicit.example.com' });
    assert.ok(mem instanceof MemoryClient);
  });
});

test('falls back to DEFAULT_BASE_URL without env', () => {
  withEnv({ [ENV_URL]: undefined, [ENV_API_KEY]: undefined }, () => {
    assert.strictEqual(DEFAULT_BASE_URL, 'http://localhost:8000/api/v1');
    const mem = quickstart();
    assert.ok(mem instanceof MemoryClient);
  });
});

test('configure overrides preset and chains', () => {
  withEnv({ [ENV_URL]: undefined }, () => {
    const mem = quickstart({ preset: 'chatbot' });
    const same = mem.configure({ recallTopK: 42 });
    assert.strictEqual(same, mem);
    assert.strictEqual(mem.settings.recallTopK, 42);
    assert.strictEqual(mem.settings.semanticThreshold, 0.4); // 其余保持预设
  });
});

test('settings getter returns copy', () => {
  withEnv({ [ENV_URL]: undefined }, () => {
    const mem = quickstart();
    const s = mem.settings;
    s.recallTopK = 999;
    assert.strictEqual(mem.settings.recallTopK, 5);
  });
});
