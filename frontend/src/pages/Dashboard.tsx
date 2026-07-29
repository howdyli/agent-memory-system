import type { CSSProperties } from 'react';
import {
  Card,
  Statistic,
  Row,
  Col,
  Tag,
  Button,
  Space,
  Badge,
  Skeleton,
  Empty,
} from 'antd';
import {
  KeyOutlined,
  DatabaseOutlined,
  FileTextOutlined,
  ShareAltOutlined,
  MessageOutlined,
  CommentOutlined,
  PlusSquareOutlined,
  FileAddOutlined,
  ThunderboltOutlined,
  RightOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import {
  useHealth,
  useVariables,
  useTables,
  useFragments,
  useGraphEntities,
  useSessionList,
} from '../hooks/useMemoryQueries';

interface SessionItem {
  session_id: string;
  title?: string;
  updated_at?: string;
  message_count?: number;
}

interface HealthData {
  status?: string;
  timestamp?: string;
  service?: string;
}

function formatDateTime(iso?: string): string {
  if (!iso) return '-';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function isToday(iso?: string): boolean {
  if (!iso) return false;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return false;
  const now = new Date();
  return (
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate()
  );
}

function safeCount(data: unknown): number | string {
  if (Array.isArray(data)) return data.length;
  if (data && typeof data === 'object') return Object.keys(data).length;
  if (typeof data === 'number') return data;
  return '-';
}

function StatCard({
  title,
  value,
  icon,
  color,
  loading,
  error,
}: {
  title: string;
  value: number | string;
  icon: React.ReactNode;
  color: string;
  loading: boolean;
  error: boolean;
}) {
  return (
    <Card variant="borderless" style={{ height: '100%' }}>
      {loading ? (
        <Skeleton active paragraph={{ rows: 1 }} title={false} />
      ) : (
        <Statistic
          title={title}
          value={error ? '-' : value}
          styles={{ content: { color } }}
          prefix={icon}
        />
      )}
    </Card>
  );
}

export default function DashboardPage() {
  const navigate = useNavigate();

  const { data: health, isLoading: healthLoading, isError: healthError } = useHealth();
  const { data: variables, isLoading: variablesLoading, isError: variablesError } = useVariables();
  const { data: tables, isLoading: tablesLoading, isError: tablesError } = useTables();
  const { data: fragments, isLoading: fragmentsLoading, isError: fragmentsError } = useFragments();
  const { data: graphEntities, isLoading: graphLoading, isError: graphError } = useGraphEntities();
  const { data: sessions, isLoading: sessionsLoading, isError: sessionsError } = useSessionList();

  const healthData = health as HealthData | undefined;
  const isHealthy = healthData?.status === 'healthy';

  const varCount = safeCount(variables);
  const tableCount = safeCount(tables);
  const fragmentCount = safeCount(fragments);
  const graphCount = safeCount(graphEntities);

  const sessionList = (sessions as SessionItem[] | undefined) || [];
  const todayCount = sessionList.filter((s) => isToday(s.updated_at)).length;
  const recentSessions = sessionList.slice(0, 5);

  const quickActions = [
    { label: '新建对话', path: '/agent-chat', icon: <CommentOutlined />, color: '#1677ff' },
    { label: '创建记忆表', path: '/tables', icon: <PlusSquareOutlined />, color: '#52c41a' },
    { label: '添加记忆片段', path: '/fragments', icon: <FileAddOutlined />, color: '#fa8c16' },
    { label: '触发记忆抽取', path: '/extraction', icon: <ThunderboltOutlined />, color: '#722ed1' },
  ];

  return (
    <div>
      <div className="page-header">
        <h2>仪表盘</h2>
        <p>Agent Memory System 运行概览</p>
      </div>

      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={24} sm={12} md={8} lg={4}>
          <StatCard
            title="记忆变量"
            value={varCount}
            icon={<KeyOutlined />}
            color="#1677ff"
            loading={variablesLoading}
            error={variablesError}
          />
        </Col>
        <Col xs={24} sm={12} md={8} lg={4}>
          <StatCard
            title="记忆表"
            value={tableCount}
            icon={<DatabaseOutlined />}
            color="#52c41a"
            loading={tablesLoading}
            error={tablesError}
          />
        </Col>
        <Col xs={24} sm={12} md={8} lg={4}>
          <StatCard
            title="记忆片段"
            value={fragmentCount}
            icon={<FileTextOutlined />}
            color="#fa8c16"
            loading={fragmentsLoading}
            error={fragmentsError}
          />
        </Col>
        <Col xs={24} sm={12} md={8} lg={4}>
          <StatCard
            title="图谱节点"
            value={graphCount}
            icon={<ShareAltOutlined />}
            color="#722ed1"
            loading={graphLoading}
            error={graphError}
          />
        </Col>
        <Col xs={24} sm={12} md={8} lg={4}>
          <StatCard
            title="今日对话"
            value={todayCount}
            icon={<MessageOutlined />}
            color="#eb2f96"
            loading={sessionsLoading}
            error={sessionsError}
          />
        </Col>
      </Row>

      <Card title="快速操作" className="section-card" style={{ marginBottom: 24 }}>
        <Space wrap size="middle">
          {quickActions.map((action) => (
            <Button
              key={action.path}
              type="primary"
              size="large"
              icon={action.icon}
              className="quick-action-btn"
              style={{ '--btn-color': action.color, backgroundColor: action.color, minWidth: 160 } as CSSProperties}
              onClick={() => navigate(action.path)}
            >
              {action.label}
            </Button>
          ))}
        </Space>
      </Card>

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={12}>
          <Card title="最近对话" className="section-card" extra={<Tag>{recentSessions.length} 条</Tag>}>
            {sessionsLoading ? (
              <Skeleton active paragraph={{ rows: 4 }} title={false} />
            ) : sessionsError ? (
              <Empty description="对话列表加载失败" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            ) : (
              <Space direction="vertical" style={{ width: '100%' }}>
                {recentSessions.length === 0 ? (
                  <Empty description="暂无对话" image={Empty.PRESENTED_IMAGE_SIMPLE} />
                ) : (
                  recentSessions.map((session: SessionItem) => (
                    <Card
                      key={session.session_id}
                      size="small"
                      styles={{ body: { display: 'flex', justifyContent: 'space-between', alignItems: 'center' } }}
                    >
                      <div>
                        <div style={{ fontWeight: 500 }}>{session.title || '未命名会话'}</div>
                        <Space size="middle" style={{ marginTop: 4 }}>
                          <span style={{ fontSize: 12, color: '#999' }}>{formatDateTime(session.updated_at)}</span>
                          <Tag color="blue">{session.message_count ?? 0} 条消息</Tag>
                        </Space>
                      </div>
                      <Button
                        type="link"
                        size="small"
                        icon={<RightOutlined />}
                        onClick={() => navigate('/agent-chat', { state: { sessionId: session.session_id } })}
                      >
                        查看
                      </Button>
                    </Card>
                  ))
                )}
              </Space>
            )}
          </Card>
        </Col>

        <Col xs={24} lg={12}>
          <Card title="系统健康状态" className="section-card">
            {healthLoading ? (
              <Skeleton active paragraph={{ rows: 3 }} title={false} />
            ) : healthError ? (
              <Empty description="健康状态获取失败" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            ) : (
              <Space direction="vertical" style={{ width: '100%' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 0', borderBottom: '1px solid #f0f0f0' }}>
                  <div>
                    <div style={{ fontWeight: 500 }}>后端服务</div>
                    <div style={{ fontSize: 12, color: '#999' }}>{healthData?.service || 'agent-memory-backend'}</div>
                  </div>
                  <Badge status={isHealthy ? 'success' : 'error'} text={isHealthy ? '健康' : '异常'} />
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 0', borderBottom: '1px solid #f0f0f0' }}>
                  <div>
                    <div style={{ fontWeight: 500 }}>数据库</div>
                    <div style={{ fontSize: 12, color: '#999' }}>SQLite 持久化存储</div>
                  </div>
                  <Badge status={isHealthy ? 'success' : 'error'} text={isHealthy ? '正常' : '异常'} />
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 0', borderBottom: '1px solid #f0f0f0' }}>
                  <div>
                    <div style={{ fontWeight: 500 }}>Redis</div>
                    <div style={{ fontSize: 12, color: '#999' }}>内存缓存与变量存储</div>
                  </div>
                  <Badge status={isHealthy ? 'success' : 'error'} text={isHealthy ? '正常' : '异常'} />
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 0', borderBottom: '1px solid #f0f0f0' }}>
                  <div>
                    <div style={{ fontWeight: 500 }}>向量库</div>
                    <div style={{ fontSize: 12, color: '#999' }}>ChromaDB 语义检索</div>
                  </div>
                  <Badge status={isHealthy ? 'success' : 'error'} text={isHealthy ? '正常' : '异常'} />
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 0' }}>
                  <div>
                    <div style={{ fontWeight: 500 }}>最后检查</div>
                    <div style={{ fontSize: 12, color: '#999' }}>{formatDateTime(healthData?.timestamp)}</div>
                  </div>
                </div>
              </Space>
            )}
          </Card>
        </Col>
      </Row>
    </div>
  );
}
