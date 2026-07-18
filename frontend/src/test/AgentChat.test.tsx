import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConfigProvider, App as AntApp } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// ------------------------------------------------------------
//  虚拟列表 mock：jsdom 无真实布局，直接返回全部虚拟行并捕获 scrollToIndex
// ------------------------------------------------------------
const { scrollToIndexSpy } = vi.hoisted(() => ({ scrollToIndexSpy: vi.fn() }));

vi.mock('@tanstack/react-virtual', () => ({
  useVirtualizer: (opts: { count: number }) => ({
    getTotalSize: () => opts.count * 120,
    getVirtualItems: () =>
      Array.from({ length: opts.count }, (_, i) => ({
        index: i,
        key: i,
        start: i * 120,
        size: 120,
      })),
    scrollToIndex: scrollToIndexSpy,
    measureElement: () => {},
  }),
}));

// 100 条历史消息，用于验证大量消息渲染不崩溃
const manyMessages = Array.from({ length: 100 }, (_, i) => ({
  role: i % 2 === 0 ? 'user' : 'assistant',
  content: `消息 ${i}`,
}));

vi.mock('../services/api', () => ({
  agentApi: { chat: vi.fn(), chatStream: vi.fn() },
  sessionsApi: {
    messages: vi.fn(() =>
      Promise.resolve({ data: { messages: manyMessages, total: 200 } }),
    ),
  },
}));

vi.mock('../hooks/useMemoryQueries', () => ({
  useDeleteSession: () => ({ mutateAsync: vi.fn() }),
  useRenameSession: () => ({ mutateAsync: vi.fn() }),
}));

vi.mock('../components/SessionSidebar', () => ({
  default: (props: { onSelect: (sid: string) => void }) => (
    <button data-testid="select-session" onClick={() => props.onSelect('s1')}>
      select
    </button>
  ),
}));

vi.mock('../components/SummaryPanel', () => ({ default: () => <div data-testid="summary" /> }));

vi.mock('../components/MarkdownRenderer', () => ({
  default: ({ content }: { content: string }) => <div>{content}</div>,
}));

import AgentChatPage from '../pages/AgentChat';

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ConfigProvider locale={zhCN}>
        <AntApp>
          <AgentChatPage />
        </AntApp>
      </ConfigProvider>
    </QueryClientProvider>,
  );
}

describe('AgentChat 虚拟列表冒烟测试', () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => cleanup());

  it('空会话应正常渲染输入框，不崩溃', () => {
    renderPage();
    expect(
      screen.getByPlaceholderText('输入消息... (Enter 发送, Shift+Enter 换行)'),
    ).toBeInTheDocument();
  });

  it('加载大量历史消息后应渲染且不崩溃', async () => {
    renderPage();
    fireEvent.click(screen.getByTestId('select-session'));
    // 首尾消息均应出现在虚拟渲染结果中
    expect(await screen.findByText('消息 0')).toBeInTheDocument();
    expect(await screen.findByText('消息 99')).toBeInTheDocument();
  });

  it('加载消息后应触发自动滚动到底部（scrollToIndex）', async () => {
    renderPage();
    fireEvent.click(screen.getByTestId('select-session'));
    await screen.findByText('消息 0');
    await waitFor(() =>
      expect(scrollToIndexSpy).toHaveBeenCalledWith(99, { align: 'end' }),
    );
  });
});
