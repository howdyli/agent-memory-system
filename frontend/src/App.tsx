import { lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { ConfigProvider, App as AntApp, Spin } from 'antd';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import zhCN from 'antd/locale/zh_CN';
import AppLayout from './components/AppLayout';
import AuthGuard from './components/AuthGuard';
import ErrorBoundary from './components/ErrorBoundary';

// Route-level lazy loading — each page is a separate chunk
const Login = lazy(() => import('./pages/Login'));
const Dashboard = lazy(() => import('./pages/Dashboard'));
const Variables = lazy(() => import('./pages/Variables'));
const Extraction = lazy(() => import('./pages/Extraction'));
const Tables = lazy(() => import('./pages/Tables'));
const Fragments = lazy(() => import('./pages/Fragments'));
const Recall = lazy(() => import('./pages/Recall'));
const LongTerm = lazy(() => import('./pages/LongTerm'));
const System = lazy(() => import('./pages/System'));
const AgentChat = lazy(() => import('./pages/AgentChat'));
const AgentTools = lazy(() => import('./pages/AgentTools'));
const Observability = lazy(() => import('./pages/Observability'));
const Lifecycle = lazy(() => import('./pages/Lifecycle'));
const GraphMemory = lazy(() => import('./pages/GraphMemory'));
const HybridSearch = lazy(() => import('./pages/HybridSearch'));

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
      staleTime: 30_000, // 30s 内不重新请求
    },
  },
});

const PageLoading = () => (
  <div style={{ display: 'flex', justifyContent: 'center', marginTop: '40vh' }}>
    <Spin size="large" />
  </div>
);

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: '#667eea', borderRadius: 6 } }}>
        <AntApp>
          <ErrorBoundary>
            <BrowserRouter>
              <Suspense fallback={<PageLoading />}>
                <Routes>
                  <Route path="/login" element={<Login />} />
                  <Route element={<AuthGuard><AppLayout /></AuthGuard>}>
                    <Route path="/" element={<Dashboard />} />
                    <Route path="/agent-chat" element={<AgentChat />} />
                    <Route path="/agent-tools" element={<AgentTools />} />
                    <Route path="/variables" element={<Variables />} />
                    <Route path="/extraction" element={<Extraction />} />
                    <Route path="/tables" element={<Tables />} />
                    <Route path="/fragments" element={<Fragments />} />
                    <Route path="/recall" element={<Recall />} />
                    <Route path="/long-term" element={<LongTerm />} />
                    <Route path="/observability" element={<Observability />} />
                    <Route path="/lifecycle" element={<Lifecycle />} />
                    <Route path="/graph-memory" element={<GraphMemory />} />
                    <Route path="/hybrid-search" element={<HybridSearch />} />
                    <Route path="/system" element={<System />} />
                  </Route>
                  <Route path="*" element={<Navigate to="/" replace />} />
                </Routes>
              </Suspense>
            </BrowserRouter>
          </ErrorBoundary>
        </AntApp>
      </ConfigProvider>
    </QueryClientProvider>
  );
}

export default App;
