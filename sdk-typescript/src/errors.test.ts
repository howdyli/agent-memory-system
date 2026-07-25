import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import {
  AgentMemoryError,
  TransportError,
  HTTPError,
  AuthenticationError,
  PermissionDeniedError,
  NotFoundError,
} from './errors.js';

describe('Error hierarchy', () => {
  test('AgentMemoryError is base class', () => {
    const err = new AgentMemoryError('test');
    assert.equal(err.message, 'test');
    assert.equal(err.name, 'AgentMemoryError');
    assert.ok(err instanceof Error);
  });

  test('TransportError extends AgentMemoryError', () => {
    const err = new TransportError('network fail');
    assert.equal(err.name, 'TransportError');
    assert.ok(err instanceof AgentMemoryError);
  });

  test('HTTPError carries statusCode and detail', () => {
    const err = new HTTPError(500, 'server error');
    assert.equal(err.statusCode, 500);
    assert.equal(err.detail, 'server error');
    assert.equal(err.name, 'HTTPError');
    assert.ok(err instanceof TransportError);
    assert.match(err.message, /HTTP 500/);
  });

  test('AuthenticationError defaults to 401', () => {
    const err = new AuthenticationError();
    assert.equal(err.statusCode, 401);
    assert.ok(err instanceof HTTPError);
  });

  test('PermissionDeniedError defaults to 403', () => {
    const err = new PermissionDeniedError();
    assert.equal(err.statusCode, 403);
    assert.ok(err instanceof HTTPError);
  });

  test('NotFoundError defaults to 404', () => {
    const err = new NotFoundError();
    assert.equal(err.statusCode, 404);
    assert.ok(err instanceof HTTPError);
  });
});
