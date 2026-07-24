import { useEffect, useState } from 'react';
import { Card, Row, Col, Statistic, Table, Tag, Tabs, Input, Button, Space, message, Descriptions, Select } from 'antd';
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, PieChart, Pie, Cell } from 'recharts';
import {
  BarChartOutlined, DatabaseOutlined, FileTextOutlined, SearchOutlined,
  ThunderboltOutlined, ExperimentOutlined, ReloadOutlined,
  HistoryOutlined, CheckCircleOutlined, LineChartOutlined, RocketOutlined
} from '@ant-design/icons';
import { observabilityApi } from '../services/api';
import { useObservabilityDashboard, useMetricsHistory } from '../hooks/useMemoryQueries';
import PerformanceTab from '../components/PerformanceTab';

const EVENT_COLORS: Record<string, string> = {
  created: '#52c41a', recalled: '#1677ff', updated: '#faad14',
  deleted: '#ff4d4f', merged: '#722ed1', cold_marked: '#13c2c2', restored: '#eb2f96',
};

const EVENT_SOURCE_COLORS: Record<string, string> = {
  extraction: 'green', recall: 'blue', lifecycle: 'purple', manual: 'orange', conversation: 'cyan', system: 'geekblue',
};

export default function ObservabilityPage() {
  const { data: dashboard, isLoading: dashLoading, refetch: refetchDash } = useObservabilityDashboard();
  const [historyDays, setHistoryDays] = useState(7);
  const { data: historyData, refetch: refetchHistory } = useMetricsHistory(historyDays);
  const metricsHistory = historyData?.snapshots || historyData || [];

  const [events, setEvents] = useState<any[]>([]);
  const [triggers, setTriggers] = useState<any[]>([]);
  const [qualityReport, setQualityReport] = useState<any>(null);
  const [loading, setLoading] = useState({ events: false, triggers: false });

  // Trace state
  const [traceId, setTraceId] = useState('');
  const [traceResult, setTraceResult] = useState<any>(null);
  const [traceLoading, setTraceLoading] = useState(false);

  // Quality state
  const [accuracyMemId, setAccuracyMemId] = useState('');
  const [accuracyResult, setAccuracyResult] = useState<any>(null);
  const [relevanceQuery, setRelevanceQuery] = useState('');
  const [relevanceResult, setRelevanceResult] = useState<any>(null);
  const [batchResult, setBatchResult] = useState<any>(null);

  // Event filter
  const [eventFilter, setEventFilter] = useState<string>('');

  useEffect(() => {
    fetchEvents();
    fetchTriggers();
  }, []);

  const fetchEvents = async (eventType?: string) => {
    setLoading(prev => ({ ...prev, events: true }));
    try {
      const res = await observabilityApi.events({ event_type: eventType });
      setEvents(res.data?.events || res.data || []);
    } catch { message.error('获取事件失败'); }
    setLoading(prev => ({ ...prev, events: false }));
  };

  const fetchTriggers = async () => {
    setLoading(prev => ({ ...prev, triggers: true }));
    try {
      const res = await observabilityApi.extractionTriggers();
      setTriggers(res.data?.triggers || res.data || []);
    } catch { /* ignore */ }
    setLoading(prev => ({ ...prev, triggers: false }));
  };

  const handleTrace = async () => {
    if (!traceId.trim()) return;
    setTraceLoading(true);
    try {
      const res = await observabilityApi.trace(traceId.trim());
      setTraceResult(res.data);
    } catch { message.error('查询追踪失败'); }
    setTraceLoading(false);
  };

  const handleAccuracy = async () => {
    if (!accuracyMemId.trim()) return;
    try {
      const res = await observabilityApi.evaluateAccuracy({ memory_id: accuracyMemId.trim() });
      setAccuracyResult(res.data);
      message.success('评估完成');
    } catch { message.error('评估失败'); }
  };

  const handleRelevance = async () => {
    if (!relevanceQuery.trim()) return;
    try {
      const res = await observabilityApi.evaluateRelevance({ query: relevanceQuery, fragments: [] });
      setRelevanceResult(res.data);
      message.success('相关性评估完成');
    } catch { message.error('评估失败'); }
  };

  const handleBatch = async () => {
    try {
      const res = await observabilityApi.batchEvaluate();
      setBatchResult(res.data);
      message.success('批量评估完成');
    } catch { message.error('批量评估失败'); }
  };

  const handleQualityReport = async (days = 30) => {
    try {
      const res = await observabilityApi.qualityReport(days);
      setQualityReport(res.data);
    } catch { message.error('获取质量报告失败'); }
  };

  // Prepare pie data
  const typeDistribution = dashboard?.type_distribution
    ? Object.entries(dashboard.type_distribution).map(([name, value]) => ({ name, value }))
    : [];

  const PIE_COLORS = ['#1677ff', '#52c41a', '#faad14', '#ff4d4f', '#722ed1', '#13c2c2'];

  return (
    <div>
      <div className="page-header">
        <h2><BarChartOutlined /> 观测中心</h2>
        <p>记忆系统运行监控、追踪与质量评估</p>
      </div>

      <Tabs defaultActiveKey="dashboard" items={[
        // ======== Tab 1: Dashboard ========
        {
          key: 'dashboard', label: <span><BarChartOutlined /> 仪表盘</span>,
          children: (
            <div>
              <Row gutter={16} style={{ marginBottom: 16 }}>
                <Col span={4}><Card loading={dashLoading} size="small"><Statistic title="记忆总量" value={dashboard?.total_memories ?? '-'} prefix={<DatabaseOutlined />} /></Card></Col>
                <Col span={4}><Card loading={dashLoading} size="small"><Statistic title="活跃记忆" value={dashboard?.active_memories ?? '-'} prefix={<FileTextOutlined />} /></Card></Col>
                <Col span={4}><Card loading={dashLoading} size="small"><Statistic title="日新增" value={dashboard?.daily_new_rate ?? '-'} prefix={<ThunderboltOutlined />} /></Card></Col>
                <Col span={4}><Card loading={dashLoading} size="small"><Statistic title="召回命中率" value={dashboard?.recall_hit_rate != null ? `${(dashboard.recall_hit_rate * 100).toFixed(1)}%` : '-'} prefix={<CheckCircleOutlined />} valueStyle={{ color: (dashboard?.recall_hit_rate || 0) > 0.5 ? '#52c41a' : '#faad14' }} /></Card></Col>
                <Col span={4}><Card loading={dashLoading} size="small"><Statistic title="存储占用" value={dashboard?.storage_mb != null ? `${dashboard.storage_mb.toFixed(2)} MB` : '-'} prefix={<DatabaseOutlined />} /></Card></Col>
                <Col span={4}><Card loading={dashLoading} size="small"><Statistic title="LLM Token(24h)" value={dashboard?.llm_tokens_24h ?? '-'} prefix={<ThunderboltOutlined />} /></Card></Col>
              </Row>
              <Row gutter={16} style={{ marginBottom: 16 }}>
                <Col span={4}><Card size="small"><Statistic title="延迟 P50" value={dashboard?.recall_latency_p50_ms != null ? `${dashboard.recall_latency_p50_ms.toFixed(1)}ms` : '-'} suffix="" /></Card></Col>
                <Col span={4}><Card size="small"><Statistic title="延迟 P99" value={dashboard?.recall_latency_p99_ms != null ? `${dashboard.recall_latency_p99_ms.toFixed(1)}ms` : '-'} suffix="" /></Card></Col>
                <Col span={4}><Card size="small"><Statistic title="LLM Token(7d)" value={dashboard?.llm_tokens_7d ?? '-'} /></Card></Col>
                <Col span={4}><Card size="small"><Statistic title="质量均分" value={dashboard?.quality_avg_score != null ? dashboard.quality_avg_score.toFixed(3) : '-'} /></Card></Col>
              </Row>
              <Row gutter={16}>
                <Col span={8}>
                  <Card title="记忆类型分布" size="small">
                    {typeDistribution.length > 0 ? (
                      <ResponsiveContainer width="100%" height={240}>
                        <PieChart>
                          <Pie data={typeDistribution} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={80} label={({ name, percent }) => `${name} ${((percent ?? 0) * 100).toFixed(0)}%`}>
                            {typeDistribution.map((_, i) => <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />)}
                          </Pie>
                          <Tooltip />
                        </PieChart>
                      </ResponsiveContainer>
                    ) : <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>暂无数据</div>}
                  </Card>
                </Col>
                <Col span={16}>
                  <Card title="最近追踪事件" size="small" extra={
                    <Space>
                      <Select value={eventFilter} onChange={v => { setEventFilter(v); fetchEvents(v || undefined); }} style={{ width: 140 }} allowClear placeholder="事件类型"
                        options={['created', 'recalled', 'updated', 'deleted', 'merged', 'cold_marked', 'restored'].map(t => ({ value: t, label: t }))} />
                      <Button size="small" icon={<ReloadOutlined />} onClick={() => fetchEvents()}>刷新</Button>
                    </Space>
                  }>
                    <Table dataSource={events.slice(0, 10)} rowKey="id" size="small" pagination={false} loading={loading.events}
                      columns={[
                        { title: 'ID', dataIndex: 'id', width: 50 },
                        { title: '类型', dataIndex: 'event_type', width: 90, render: (t: string) => <Tag color={EVENT_COLORS[t]}>{t}</Tag> },
                        { title: '来源', dataIndex: 'event_source', width: 80, render: (s: string) => <Tag color={EVENT_SOURCE_COLORS[s]}>{s}</Tag> },
                        { title: '记忆 ID', dataIndex: 'memory_id', width: 120, ellipsis: true },
                        { title: '延迟(ms)', dataIndex: 'latency_ms', width: 80, render: (v: number) => v?.toFixed(1) || '-' },
                        { title: '分数', dataIndex: 'score', width: 70, render: (v: number) => v?.toFixed(2) || '-' },
                        { title: '时间', dataIndex: 'created_at', width: 150, render: (v: string) => v ? new Date(v).toLocaleString() : '-' },
                      ]} />
                  </Card>
                </Col>
              </Row>
              <div style={{ marginTop: 12, textAlign: 'right' }}>
                <Button icon={<ReloadOutlined />} onClick={() => refetchDash()}>刷新</Button>
              </div>
            </div>
          ),
        },

        // ======== Tab 2: Metrics History ========
        {
          key: 'history', label: <span><LineChartOutlined /> 指标历史</span>,
          children: (
            <Card title="时间序列趋势"
              extra={
                <Space>
                  <Select value={historyDays} onChange={v => setHistoryDays(v)} style={{ width: 100 }}
                    options={[{ value: 7, label: '7天' }, { value: 14, label: '14天' }, { value: 30, label: '30天' }]} />
                  <Button icon={<ReloadOutlined />} onClick={() => refetchHistory()}>刷新</Button>
                </Space>
              }>
              {metricsHistory.length > 0 ? (
                <div>
                  <ResponsiveContainer width="100%" height={250}>
                    <AreaChart data={metricsHistory}>
                      <CartesianGrid strokeDasharray="3 3" />
                      <XAxis dataKey="snapshot_time" tickFormatter={(v: string) => v?.substring(5, 16) || ''} fontSize={11} />
                      <YAxis />
                      <Tooltip />
                      <Area type="monotone" dataKey="total_memories" stroke="#1677ff" fill="#1677ff" fillOpacity={0.1} name="总记忆量" />
                      <Area type="monotone" dataKey="daily_new_count" stroke="#52c41a" fill="#52c41a" fillOpacity={0.1} name="日新增" />
                    </AreaChart>
                  </ResponsiveContainer>
                  <ResponsiveContainer width="100%" height={200}>
                    <AreaChart data={metricsHistory}>
                      <CartesianGrid strokeDasharray="3 3" />
                      <XAxis dataKey="snapshot_time" tickFormatter={(v: string) => v?.substring(5, 16) || ''} fontSize={11} />
                      <YAxis />
                      <Tooltip />
                      <Area type="monotone" dataKey="avg_recall_latency_ms" stroke="#faad14" fill="#faad14" fillOpacity={0.1} name="平均延迟(ms)" />
                      <Area type="monotone" dataKey="p99_recall_latency_ms" stroke="#ff4d4f" fill="#ff4d4f" fillOpacity={0.1} name="P99延迟(ms)" />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              ) : <div style={{ textAlign: 'center', padding: 60, color: '#999' }}>暂无指标历史数据<Button type="link" onClick={() => observabilityApi.snapshot().then(() => { message.success('快照已创建'); refetchHistory(); })}>创建快照</Button></div>}
              <div style={{ marginTop: 12 }}><Button icon={<ExperimentOutlined />} onClick={() => observabilityApi.snapshot().then(() => { message.success('快照已创建'); refetchHistory(); })}>创建快照</Button></div>
            </Card>
          ),
        },

        // ======== Tab 3: Memory Trace ========
        {
          key: 'trace', label: <span><SearchOutlined /> 记忆追踪</span>,
          children: (
            <Card title="全链路追踪">
              <Space.Compact style={{ width: '100%', marginBottom: 16 }}>
                <Input value={traceId} onChange={e => setTraceId(e.target.value)} placeholder="输入记忆 ID (memory_id)..." onPressEnter={handleTrace} />
                <Button type="primary" icon={<SearchOutlined />} loading={traceLoading} onClick={handleTrace}>查询</Button>
              </Space.Compact>
              {traceResult?.success === false && <div style={{ color: '#ff4d4f' }}>查询失败: {traceResult.error}</div>}
              {traceResult?.success && (
                <div>
                  <Descriptions size="small" column={4} bordered style={{ marginBottom: 16 }}>
                    <Descriptions.Item label="总事件数">{traceResult.summary?.total_events}</Descriptions.Item>
                    <Descriptions.Item label="召回次数">{traceResult.summary?.times_recalled}</Descriptions.Item>
                    <Descriptions.Item label="更新次数">{traceResult.summary?.times_updated}</Descriptions.Item>
                    <Descriptions.Item label="当前状态"><Tag color={traceResult.summary?.current_status === 'active' ? 'green' : 'red'}>{traceResult.summary?.current_status}</Tag></Descriptions.Item>
                    <Descriptions.Item label="首次出现">{traceResult.summary?.first_seen ? new Date(traceResult.summary.first_seen).toLocaleString() : '-'}</Descriptions.Item>
                    <Descriptions.Item label="最后出现">{traceResult.summary?.last_seen ? new Date(traceResult.summary.last_seen).toLocaleString() : '-'}</Descriptions.Item>
                  </Descriptions>
                  <Table dataSource={traceResult.events} rowKey="id" size="small" pagination={false}
                    columns={[
                      { title: '事件', dataIndex: 'event_type', width: 100, render: (t: string) => <Tag color={EVENT_COLORS[t]}>{t}</Tag> },
                      { title: '来源', dataIndex: 'event_source', width: 80, render: (s: string) => <Tag color={EVENT_SOURCE_COLORS[s]}>{s}</Tag> },
                      { title: '对话', dataIndex: 'conversation_id', width: 100, ellipsis: true },
                      { title: '分数', dataIndex: 'score', width: 70, render: (v: number) => v?.toFixed(2) || '-' },
                      { title: '延迟(ms)', dataIndex: 'latency_ms', width: 80, render: (v: number) => v?.toFixed(1) || '-' },
                      { title: '时间', dataIndex: 'created_at', width: 150, render: (v: string) => v ? new Date(v).toLocaleString() : '-' },
                    ]} />
                </div>
              )}
            </Card>
          ),
        },

        // ======== Tab 4: Trace Events ========
        {
          key: 'events', label: <span><HistoryOutlined /> 追踪事件</span>,
          children: (
            <Card title="事件列表" extra={
              <Space>
                <Select value={eventFilter} onChange={v => { setEventFilter(v); fetchEvents(v || undefined); }} style={{ width: 140 }} allowClear placeholder="事件类型"
                  options={['created', 'recalled', 'updated', 'deleted', 'merged', 'cold_marked', 'restored'].map(t => ({ value: t, label: t }))} />
                <Button icon={<ReloadOutlined />} onClick={() => fetchEvents()}>刷新</Button>
              </Space>
            }>
              <Table dataSource={events} rowKey="id" size="small" loading={loading.events}
                columns={[
                  { title: 'ID', dataIndex: 'id', width: 50 },
                  { title: '类型', dataIndex: 'event_type', width: 90, render: (t: string) => <Tag color={EVENT_COLORS[t]}>{t}</Tag> },
                  { title: '来源', dataIndex: 'event_source', width: 80, render: (s: string) => <Tag color={EVENT_SOURCE_COLORS[s]}>{s}</Tag> },
                  { title: '记忆 ID', dataIndex: 'memory_id', width: 120, ellipsis: true },
                  { title: '记忆类型', dataIndex: 'memory_type', width: 70 },
                  { title: '会话', dataIndex: 'session_id', width: 100, ellipsis: true },
                  { title: '对话', dataIndex: 'conversation_id', width: 100, ellipsis: true },
                  { title: '分数', dataIndex: 'score', width: 70, render: (v: number) => v?.toFixed(2) || '-' },
                  { title: '延迟(ms)', dataIndex: 'latency_ms', width: 80, render: (v: number) => v?.toFixed(1) || '-' },
                  { title: '时间', dataIndex: 'created_at', width: 150, render: (v: string) => v ? new Date(v).toLocaleString() : '-' },
                ]} />
            </Card>
          ),
        },

        // ======== Tab 5: Extraction Triggers ========
        {
          key: 'triggers', label: <span><ThunderboltOutlined /> 抽取触发</span>,
          children: (
            <Card title="抽取触发记录" extra={<Button icon={<ReloadOutlined />} onClick={fetchTriggers}>刷新</Button>}>
              <Table dataSource={triggers} rowKey="id" size="small" loading={loading.triggers}
                columns={[
                  { title: 'ID', dataIndex: 'id', width: 50 },
                  { title: '触发类型', dataIndex: 'trigger_type', width: 110, render: (t: string) => <Tag>{t}</Tag> },
                  { title: '会话', dataIndex: 'session_id', width: 120, ellipsis: true },
                  { title: '对话', dataIndex: 'conversation_id', width: 120, ellipsis: true },
                  { title: '查询片段', dataIndex: 'query_snippet', ellipsis: true },
                  { title: '创建片段数', dataIndex: 'fragments_created', width: 90 },
                  { title: 'Token 消耗', dataIndex: 'llm_tokens_used', width: 90 },
                  { title: '时间', dataIndex: 'created_at', width: 150, render: (v: string) => v ? new Date(v).toLocaleString() : '-' },
                ]} />
            </Card>
          ),
        },

        // ======== Tab 6: Performance ========
        {
          key: 'performance', label: <span><RocketOutlined /> 性能指标</span>,
          children: <PerformanceTab />,
        },

        // ======== Tab 7: Quality Assessment ========
        {
          key: 'quality', label: <span><CheckCircleOutlined /> 质量评估</span>,
          children: (
            <Row gutter={16}>
              <Col span={12}>
                <Card title="准确率评估" size="small" style={{ marginBottom: 16 }}>
                  <Space.Compact style={{ width: '100%', marginBottom: 8 }}>
                    <Input value={accuracyMemId} onChange={e => setAccuracyMemId(e.target.value)} placeholder="输入 memory_id..." />
                    <Button type="primary" onClick={handleAccuracy}>评估</Button>
                  </Space.Compact>
                  {accuracyResult?.success && (
                    <Descriptions size="small" column={1} bordered>
                      <Descriptions.Item label="内容">{accuracyResult.memory_content}</Descriptions.Item>
                      <Descriptions.Item label="得分">{accuracyResult.score}</Descriptions.Item>
                      <Descriptions.Item label="评估方式">{accuracyResult.evaluator}</Descriptions.Item>
                      <Descriptions.Item label="理由">{accuracyResult.reason}</Descriptions.Item>
                    </Descriptions>
                  )}
                </Card>
                <Card title="批量评估" size="small">
                  <Button type="primary" onClick={handleBatch} style={{ marginBottom: 8 }}>评估最近创建的 10 条</Button>
                  {batchResult?.success && (
                    <div>批量评估: {batchResult.count} 条, 平均分: {batchResult.avg_score?.toFixed(3)}</div>
                  )}
                </Card>
              </Col>
              <Col span={12}>
                <Card title="召回相关性评估" size="small" style={{ marginBottom: 16 }}>
                  <Input.TextArea value={relevanceQuery} onChange={e => setRelevanceQuery(e.target.value)} rows={3} placeholder="输入查询文本..." style={{ marginBottom: 8 }} />
                  <Button type="primary" onClick={handleRelevance}>评估</Button>
                  {relevanceResult?.success && (
                    <div style={{ marginTop: 8 }}>
                      <p>平均分: {relevanceResult.avg_score?.toFixed(3)} | 评估数: {relevanceResult.count}</p>
                    </div>
                  )}
                </Card>
                <Card title="质量报告" size="small" extra={
                  <Space>
                    <Button size="small" onClick={() => handleQualityReport(7)}>7天</Button>
                    <Button size="small" type="primary" onClick={() => handleQualityReport(30)}>30天</Button>
                  </Space>
                }>
                  {qualityReport?.success ? (
                    <Descriptions size="small" column={2} bordered>
                      <Descriptions.Item label="评估总数">{qualityReport.total_evaluations}</Descriptions.Item>
                      <Descriptions.Item label="评估类型">{(qualityReport.evaluation_types || []).join(', ')}</Descriptions.Item>
                      <Descriptions.Item label="平均分">{qualityReport.avg_score?.toFixed(3)}</Descriptions.Item>
                      <Descriptions.Item label="最高分">{qualityReport.max_score?.toFixed(3)}</Descriptions.Item>
                      <Descriptions.Item label="最低分">{qualityReport.min_score?.toFixed(3)}</Descriptions.Item>
                    </Descriptions>
                  ) : <div style={{ textAlign: 'center', padding: 30, color: '#999' }}>点击按钮加载质量报告</div>}
                </Card>
              </Col>
            </Row>
          ),
        },
      ]} />
    </div>
  );
}
