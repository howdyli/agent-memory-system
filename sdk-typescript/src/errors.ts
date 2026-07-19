/**
 * SDK error classes.
 */

export class AgentMemoryError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'AgentMemoryError';
  }
}

export class TransportError extends AgentMemoryError {
  constructor(message: string) {
    super(message);
    this.name = 'TransportError';
  }
}

export class HTTPError extends TransportError {
  public readonly statusCode: number;
  public readonly detail: string;

  constructor(statusCode: number, detail: string = '') {
    super(`HTTP ${statusCode}: ${detail}`);
    this.name = 'HTTPError';
    this.statusCode = statusCode;
    this.detail = detail;
  }
}

export class AuthenticationError extends HTTPError {
  constructor(detail: string = 'Authentication failed') {
    super(401, detail);
    this.name = 'AuthenticationError';
  }
}

export class PermissionDeniedError extends HTTPError {
  constructor(detail: string = 'Permission denied') {
    super(403, detail);
    this.name = 'PermissionDeniedError';
  }
}

export class NotFoundError extends HTTPError {
  constructor(detail: string = 'Not found') {
    super(404, detail);
    this.name = 'NotFoundError';
  }
}
