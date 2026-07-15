import '@testing-library/jest-dom';
import { vi } from 'vitest';

// Mock matchMedia (required by antd)
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
});

// Mock ResizeObserver (required by antd Table)
class ResizeObserverStub {
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
}
Object.defineProperty(window, 'ResizeObserver', { writable: true, value: ResizeObserverStub });

// Mock scrollTo
Object.defineProperty(window, 'scrollTo', { writable: true, value: vi.fn() });
