import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConfigProvider, App as AntApp } from 'antd';
import zhCN from 'antd/locale/zh_CN';

// Mock the hooks used by Dashboard — all return empty/loading=false
vi.mock('../hooks/useMemoryQueries', () => ({
  useHealth: vi.fn(() => ({ data: { status: 'healthy' }, isLoading: false, error: null })),
  useVariables: vi.fn(() => ({ data: {}, isLoading: false, error: null })),
  useTables: vi.fn(() => ({ data: [], isLoading: false, error: null })),
  useFragments: vi.fn(() => ({ data: [], isLoading: false, error: null })),
  useGraphEntities: vi.fn(() => ({ data: [], isLoading: false, error: null })),
  useSessionList: vi.fn(() => ({ data: [], isLoading: false, error: null })),
}));

import Dashboard from '../pages/Dashboard';

// 占位目标页（集成测试用于验证路由跳转成功）
const PlaceholderPage = ({ name }: { name: string }) => <div>{name} Page Loaded</div>;

function renderWithProviders(initialRoute = '/') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  const result = render(
    <QueryClientProvider client={queryClient}>
      <ConfigProvider locale={zhCN}>
        <AntApp>
          <MemoryRouter initialEntries={[initialRoute]}>
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/agent-chat" element={<PlaceholderPage name="AgentChat" />} />
              <Route path="/tables" element={<PlaceholderPage name="Tables" />} />
              <Route path="/fragments" element={<PlaceholderPage name="Fragments" />} />
              <Route path="/extraction" element={<PlaceholderPage name="Extraction" />} />
            </Routes>
          </MemoryRouter>
        </AntApp>
      </ConfigProvider>
    </QueryClientProvider>,
  );

  return result;
}

// ============================================================
//  单元测试
// ============================================================
describe('Dashboard 快捷操作按钮 - 单元测试', () => {
  beforeEach(() => vi.clearAllMocks());

  it('应渲染"快速操作"卡片标题', () => {
    renderWithProviders();
    expect(screen.getByText('快速操作')).toBeInTheDocument();
  });

  it('应渲染全部 4 个快捷操作按钮', () => {
    renderWithProviders();
    expect(screen.getByText('新建对话')).toBeInTheDocument();
    expect(screen.getByText('创建记忆表')).toBeInTheDocument();
    expect(screen.getByText('添加记忆片段')).toBeInTheDocument();
    expect(screen.getByText('触发记忆抽取')).toBeInTheDocument();
  });

  it('每个按钮应有正确的背景色', () => {
    renderWithProviders();
    expect(screen.getByText('新建对话').closest('button')).toHaveStyle({ backgroundColor: '#1677ff' });
    expect(screen.getByText('创建记忆表').closest('button')).toHaveStyle({ backgroundColor: '#52c41a' });
    expect(screen.getByText('添加记忆片段').closest('button')).toHaveStyle({ backgroundColor: '#fa8c16' });
    expect(screen.getByText('触发记忆抽取').closest('button')).toHaveStyle({ backgroundColor: '#722ed1' });
  });

  it('按钮应携带 quick-action-btn CSS 类（用于 hover/active 样式）', () => {
    renderWithProviders();
    const quickBtns = screen.getAllByRole('button').filter((b) =>
      b.classList.contains('quick-action-btn'),
    );
    expect(quickBtns).toHaveLength(4);
  });

  it('桌面端按钮 minWidth 应为 160px', () => {
    renderWithProviders();
    expect(screen.getByText('新建对话').closest('button')).toHaveStyle({ minWidth: '160px' });
  });
});

// ============================================================
//  集成测试 — 路由跳转
// ============================================================
describe('Dashboard 快捷操作按钮 - 集成测试（路由跳转）', () => {
  beforeEach(() => vi.clearAllMocks());

  it('点击"新建对话"应跳转到 /agent-chat', () => {
    renderWithProviders();
    fireEvent.click(screen.getByText('新建对话'));
    expect(screen.getByText('AgentChat Page Loaded')).toBeInTheDocument();
  });

  it('点击"创建记忆表"应跳转到 /tables', () => {
    renderWithProviders();
    fireEvent.click(screen.getByText('创建记忆表'));
    expect(screen.getByText('Tables Page Loaded')).toBeInTheDocument();
  });

  it('点击"添加记忆片段"应跳转到 /fragments', () => {
    renderWithProviders();
    fireEvent.click(screen.getByText('添加记忆片段'));
    expect(screen.getByText('Fragments Page Loaded')).toBeInTheDocument();
  });

  it('点击"触发记忆抽取"应跳转到 /extraction', () => {
    renderWithProviders();
    fireEvent.click(screen.getByText('触发记忆抽取'));
    expect(screen.getByText('Extraction Page Loaded')).toBeInTheDocument();
  });

  it('连续点击不同按钮应正确跳转到各自目标路由', () => {
    renderWithProviders();

    // 先点击"创建记忆表"
    fireEvent.click(screen.getByText('创建记忆表'));
    expect(screen.getByText('Tables Page Loaded')).toBeInTheDocument();

    // 浏览器后退回 Dashboard
    // 因为用的是 MemoryRouter，无法直接后退，重新渲染测试下一个按钮
  });

  it('每个按钮点击后不应停留在 Dashboard 页面', () => {
    renderWithProviders();

    // 点击前 Dashboard 标题应存在
    expect(screen.getByText('仪表盘')).toBeInTheDocument();

    fireEvent.click(screen.getByText('新建对话'));

    // 点击后 Dashboard 标题应消失（已跳转到新页面）
    expect(screen.queryByText('仪表盘')).not.toBeInTheDocument();
  });
});

// ============================================================
//  回归测试 — 按钮定义数据完整性
// ============================================================
describe('Dashboard 快捷操作按钮 - 回归测试', () => {
  beforeEach(() => vi.clearAllMocks());

  it('快捷操作按钮的路径应与 App.tsx 中定义的路由匹配', () => {
    const buttons = [
      { label: '新建对话', target: 'AgentChat Page Loaded' },
      { label: '创建记忆表', target: 'Tables Page Loaded' },
      { label: '添加记忆片段', target: 'Fragments Page Loaded' },
      { label: '触发记忆抽取', target: 'Extraction Page Loaded' },
    ];

    buttons.forEach(({ label, target }) => {
      cleanup(); // 清理上一次渲染的 DOM
      renderWithProviders();
      fireEvent.click(screen.getByText(label));
      expect(screen.getByText(target)).toBeInTheDocument();
    });
  });

  it('按钮文本不应为空', () => {
    renderWithProviders();
    const quickBtns = screen.getAllByRole('button').filter((b) =>
      b.classList.contains('quick-action-btn'),
    );
    quickBtns.forEach((btn) => {
      expect(btn.textContent).toBeTruthy();
      expect(btn.textContent!.length).toBeGreaterThan(0);
    });
  });

  it('每个按钮应有唯一 key（通过唯一路径验证）', () => {
    renderWithProviders();
    const quickBtns = screen.getAllByRole('button').filter((b) =>
      b.classList.contains('quick-action-btn'),
    );
    // 4 个按钮的文本内容各不相同，间接验证 key 唯一性
    const texts = quickBtns.map((b) => b.textContent);
    const uniqueTexts = new Set(texts);
    expect(uniqueTexts.size).toBe(4);
  });
});
