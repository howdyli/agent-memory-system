import { useState } from 'react';
import { Card, Input, Button, Space, Tabs, Descriptions, Tag, Slider, Switch, message, Spin, Empty, Row, Col, Tooltip } from 'antd';
import { SearchOutlined, ThunderboltOutlined, SettingOutlined, BarChartOutlined } from '@ant-design/icons';
import { recallApi } from '../services/api';
import { useRecallConfig, useRecallStats, useUpdateRecallConfig } from '../hooks/useMemoryQueries';

export default function RecallPage() {
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(false);
  const [recallResult, setRecallResult] = useState<any>(null);
  const [searchResult, setSearchResult] = useState<any>(null);
  const [summary, setSummary] = useState<string>('');
  const [topK, setTopK] = useState(10);

  const { data: config = {}, refetch: refetchConfig } = useRecallConfig();
  const { data: stats = {}, refetch: refetchStats } = useRecallStats();
  const updateConfig = useUpdateRecallConfig();

  const handleToggleHybrid = async (checked: boolean) => {
    try {
      await updateConfig.mutateAsync({ use_hybrid_search: checked });
      message.success(`混合搜索已${checked ? '开启' : '关闭'}`);
    } catch { message.error('配置更新失败'); }
  };

  const handleWeightChange = async (key: string, value: number) => {
    try { await updateConfig.mutateAsync({ [key]: value }); }
    catch { message.error('权重更新失败'); }
  };

  const handleRecall = async () => {
    if (!query.trim()) { message.warning('请输入查询'); return; }
    setLoading(true);
    try { const res = await recallApi.auto(query); setRecallResult(res.data); message.success('召回完成'); }
    catch { message.error('召回失败'); }
    setLoading(false);
  };

  const handleSearch = async () => {
    if (!query.trim()) return;
    setLoading(true);
    try { const res = await recallApi.search(query, topK); setSearchResult(res.data); }
    catch { message.error('搜索失败'); }
    setLoading(false);
  };

  const handleSummary = async () => {
    setLoading(true);
    try { const res = await recallApi.summary(); setSummary(typeof res.data === 'string' ? res.data : JSON.stringify(res.data, null, 2)); }
    catch { message.error('获取失败'); }
    setLoading(false);
  };

  const renderMemories = (data: unknown) => {
    const memories = (data as Record<string, unknown>)?.memories || data;
    if (Array.isArray(memories)) {
      if (memories.length === 0) return <Empty description="未找到相关记忆" />;
      return memories.map((m: Record<string, unknown>, i: number) => (
        <Card key={i} size="small" style={{ marginBottom: 8 }}>
          <Descriptions size="small" column={2}>
            <Descriptions.Item label="类型">{String(m.type || m.memory_type || '-')}</Descriptions.Item>
            <Descriptions.Item label="重要性">{Number(m.importance_score || m.score || 0).toFixed(2)}</Descriptions.Item>
            <Descriptions.Item label="内容" span={2}>{String(m.content || JSON.stringify(m))}</Descriptions.Item>
          </Descriptions>
        </Card>
      ));
    }
    return <pre className="code-block">{JSON.stringify(data, null, 2)}</pre>;
  };

  return (
    <div>
      <div className="page-header">
        <h2><ThunderboltOutlined /> 自动召回</h2>
        <p>根据查询自动召回相关记忆，含语义搜索和优先级排序</p>
      </div>

      <Tabs items={[
        {
          key: 'recall', label: '智能召回',
          children: (
            <Spin spinning={loading}>
              <Card className="section-card">
                <Space.Compact style={{ width: '100%' }}>
                  <Input value={query} onChange={e => setQuery(e.target.value)} placeholder="输入查询内容，系统自动召回相关记忆..." onPressEnter={handleRecall} />
                  <Button type="primary" icon={<ThunderboltOutlined />} onClick={handleRecall}>召回</Button>
                </Space.Compact>
              </Card>
              {recallResult && <Card title="召回结果" className="section-card">{renderMemories(recallResult)}</Card>}
            </Spin>
          ),
        },
        {
          key: 'search', label: '语义搜索',
          children: (
            <Spin spinning={loading}>
              <Card className="section-card">
                <Space.Compact style={{ width: '100%', marginBottom: 12 }}>
                  <Input value={query} onChange={e => setQuery(e.target.value)} placeholder="输入搜索关键词..." onPressEnter={handleSearch} />
                  <Button type="primary" icon={<SearchOutlined />} onClick={handleSearch}>搜索</Button>
                </Space.Compact>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                  <span>Top-K:</span>
                  <Slider min={1} max={50} value={topK} onChange={setTopK} style={{ width: 200 }} />
                  <Tag>{topK}</Tag>
                </div>
              </Card>
              {searchResult && <Card title="搜索结果" className="section-card">{renderMemories(searchResult)}</Card>}
            </Spin>
          ),
        },
        {
          key: 'summary', label: '记忆摘要',
          children: (
            <Spin spinning={loading}>
              <Card className="section-card" extra={<Button onClick={handleSummary}>生成摘要</Button>}>
                {summary ? <pre className="code-block">{summary}</pre> : <Empty description="点击按钮生成记忆摘要" />}
              </Card>
            </Spin>
          ),
        },
        {
          key: 'config', label: '召回配置',
          children: (
            <Card className="section-card" extra={<Button icon={<SettingOutlined />} onClick={() => refetchConfig()}>刷新配置</Button>}>
              {Object.keys(config).length > 0 ? (
                <div>
                  <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
                    <Col span={24}>
                      <Card size="small" title="混合搜索 (BM25 + 语义 + 实体 + 时间衰减)">
                        <Space>
                          <Tooltip title="开启后将融合多信号检索，提高召回准确率">
                            <Switch
                              checked={config.use_hybrid_search ?? true}
                              onChange={handleToggleHybrid}
                              loading={updateConfig.isPending}
                            />
                          </Tooltip>
                          <Tag color={config.use_hybrid_search ? 'green' : 'default'}>
                            {config.use_hybrid_search ? '已启用' : '未启用'}
                          </Tag>
                        </Space>
                      </Card>
                    </Col>
                  </Row>
                  <Descriptions column={2} bordered size="small">
                    {Object.entries(config).map(([k, v]) => (
                      <Descriptions.Item key={k} label={k}>{String(v)}</Descriptions.Item>
                    ))}
                  </Descriptions>
                </div>
              ) : <Empty description="点击加载配置" />}
            </Card>
          ),
        },
        {
          key: 'stats', label: '召回统计',
          children: (
            <Card className="section-card" extra={<Button icon={<BarChartOutlined />} onClick={() => refetchStats()}>刷新统计</Button>}>
              {Object.keys(stats).length > 0 ? (
                <Descriptions column={2} bordered size="small">
                  {Object.entries(stats).map(([k, v]) => (
                    <Descriptions.Item key={k} label={k}>{String(v)}</Descriptions.Item>
                  ))}
                </Descriptions>
              ) : <Empty description="点击加载统计" />}
            </Card>
          ),
        },
      ]} />
    </div>
  );
}
