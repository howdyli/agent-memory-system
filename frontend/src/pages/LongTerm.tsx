import { useState } from 'react';
import { Card, Table, Button, Space, Tabs, Descriptions, Tag, message, Empty, Popconfirm } from 'antd';
import { HistoryOutlined, RollbackOutlined, LikeOutlined, DislikeOutlined, BarChartOutlined } from '@ant-design/icons';
import { longTermApi } from '../services/api';

export default function LongTermPage() {
  const [memories, setMemories] = useState<Record<string, unknown>[]>([]);
  const [memoriesLoading, setMemoriesLoading] = useState(false);
  const [versions, setVersions] = useState<Record<string, unknown>[]>([]);
  const [versionsLoading, setVersionsLoading] = useState(false);
  const [auditLog, setAuditLog] = useState<Record<string, unknown>[]>([]);
  const [auditLoading, setAuditLoading] = useState(false);
  const [stats, setStats] = useState<Record<string, unknown>>({});
  const [selectedMemory, setSelectedMemory] = useState<Record<string, unknown> | null>(null);

  const fetchMemories = async () => {
    setMemoriesLoading(true);
    try { const res = await longTermApi.allMemories(); setMemories(res.data?.memories || res.data || []); }
    catch { message.error('获取失败'); }
    setMemoriesLoading(false);
  };

  const fetchVersions = async (memoryType: string, memoryId: string) => {
    setVersionsLoading(true);
    try { const res = await longTermApi.versionHistory(memoryType, memoryId); setVersions(res.data?.versions || res.data || []); }
    catch { message.error('获取版本历史失败'); }
    setVersionsLoading(false);
  };

  const fetchAuditLog = async () => {
    setAuditLoading(true);
    try { const res = await longTermApi.auditLog(); setAuditLog(res.data?.logs || res.data || []); }
    catch { message.error('获取审计日志失败'); }
    setAuditLoading(false);
  };

  const handleFeedback = async (memoryType: string, memoryId: string, feedbackType: string) => {
    try { await longTermApi.feedback({ memory_type: memoryType, memory_id: memoryId, feedback_type: feedbackType }); message.success('反馈已提交'); }
    catch { message.error('反馈提交失败'); }
  };

  const handleRollback = async (memoryType: string, memoryId: string, versionId: number) => {
    try { await longTermApi.rollback(memoryType, memoryId, versionId); message.success('已回滚'); fetchVersions(memoryType, memoryId); }
    catch { message.error('回滚失败'); }
  };

  const handleAutoAdjust = async () => {
    try { const res = await longTermApi.autoAdjust(); message.success(`已调整 ${res.data?.adjusted_count || 0} 条记忆权重`); }
    catch { message.error('调整失败'); }
  };

  const handleStats = async () => {
    try { const res = await longTermApi.improvementStats(); setStats(res.data); }
    catch { message.error('获取失败'); }
  };

  // (removed unused handleBatchDelete)

  return (
    <div>
      <div className="page-header">
        <h2><HistoryOutlined /> 长期记忆管理</h2>
        <p>版本控制、反馈优化、自我改进的长期记忆管理</p>
      </div>

      <Tabs items={[
        {
          key: 'memories', label: '所有记忆',
          children: (
            <Card className="section-card" extra={<Button onClick={fetchMemories}>刷新</Button>}>
              <Table
                dataSource={memories}
                rowKey={(r) => String(r.id || r.memory_id || Math.random())}
                loading={memoriesLoading}
                size="small"
                columns={[
                  { title: '类型', dataIndex: 'type', width: 100, render: (t: string) => <Tag>{t || 'variable'}</Tag> },
                  { title: 'ID', dataIndex: 'id', width: 60 },
                  { title: '内容', dataIndex: 'content', ellipsis: true, render: (v: unknown, r) => String(v || r.value || r.key || JSON.stringify(r)) },
                  { title: '重要性', dataIndex: 'importance_score', width: 80, render: (v: number) => v?.toFixed(2) || '-' },
                  { title: '操作', width: 200, render: (_, r) => (
                    <Space>
                      <Button size="small" icon={<LikeOutlined />} onClick={() => handleFeedback(String(r.type || ''), String(r.id), 'positive')} />
                      <Button size="small" icon={<DislikeOutlined />} onClick={() => handleFeedback(String(r.type || ''), String(r.id), 'negative')} />
                      <Button size="small" icon={<HistoryOutlined />} onClick={() => { setSelectedMemory(r); fetchVersions(String(r.type || ''), String(r.id)); }}>版本</Button>
                    </Space>
                  )},
                ]}
                locale={{ emptyText: '暂无记忆，请先使用其他功能创建记忆' }}
              />
            </Card>
          ),
        },
        {
          key: 'versions', label: '版本控制',
          children: (
            <Card className="section-card" extra={selectedMemory && <Tag color="blue">{String(selectedMemory.type)} #{String(selectedMemory.id)}</Tag>}>
              {!selectedMemory ? <Empty description="请先在「所有记忆」中点击某条记忆的「版本」按钮" /> : (
                <Table
                  dataSource={versions}
                  rowKey={(r) => String(r.version_id || r.id || Math.random())}
                  loading={versionsLoading}
                  size="small"
                  columns={[
                    { title: '版本', dataIndex: 'version_id', width: 60 },
                    { title: '变更类型', dataIndex: 'change_type', width: 100, render: (t: string) => <Tag>{t}</Tag> },
                    { title: '变更数据', dataIndex: 'change_data', ellipsis: true, render: (v: unknown) => JSON.stringify(v) },
                    { title: '时间', dataIndex: 'created_at', width: 130, render: (v: string) => v ? new Date(v).toLocaleString() : '-' },
                    { title: '操作', width: 80, render: (_, r) => (
                      <Popconfirm title="确认回滚到此版本？" onConfirm={() => handleRollback(String(selectedMemory?.type || ''), String(selectedMemory?.id), r.version_id as number)}>
                        <Button size="small" icon={<RollbackOutlined />}>回滚</Button>
                      </Popconfirm>
                    )},
                  ]}
                  locale={{ emptyText: '暂无版本记录' }}
                />
              )}
            </Card>
          ),
        },
        {
          key: 'feedback', label: '自我改进',
          children: (
            <Card className="section-card" extra={
              <Space>
                <Button icon={<BarChartOutlined />} onClick={handleStats}>效果统计</Button>
                <Button type="primary" onClick={handleAutoAdjust}>自动调整权重</Button>
              </Space>
            }>
              <p style={{ marginBottom: 16, color: '#888' }}>
                系统根据用户的正/负反馈自动调整记忆权重。正面反馈提升重要性，负面反馈降低重要性。
              </p>
              {Object.keys(stats).length > 0 && (
                <Descriptions column={2} bordered size="small">
                  {Object.entries(stats).map(([k, v]) => (
                    <Descriptions.Item key={k} label={k}>{String(v)}</Descriptions.Item>
                  ))}
                </Descriptions>
              )}
            </Card>
          ),
        },
        {
          key: 'audit', label: '审计日志',
          children: (
            <Card className="section-card" extra={<Button onClick={fetchAuditLog}>加载日志</Button>}>
              <Table
                dataSource={auditLog}
                rowKey={(r) => String(r.id || Math.random())}
                loading={auditLoading}
                size="small"
                columns={[
                  { title: '时间', dataIndex: 'created_at', width: 150, render: (v: string) => v ? new Date(v).toLocaleString() : '-' },
                  { title: '类型', dataIndex: 'memory_type', width: 80, render: (t: string) => <Tag>{t}</Tag> },
                  { title: '操作', dataIndex: 'change_type', width: 80, render: (t: string) => <Tag color="blue">{t}</Tag> },
                  { title: '详情', dataIndex: 'change_data', ellipsis: true, render: (v: unknown) => JSON.stringify(v) },
                ]}
                locale={{ emptyText: '暂无审计日志' }}
              />
            </Card>
          ),
        },
      ]} />
    </div>
  );
}
