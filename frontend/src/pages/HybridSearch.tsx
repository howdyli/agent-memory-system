import { useState, useEffect, useMemo } from 'react';
import {
  Card,
  Row,
  Col,
  Table,
  Tag,
  Tabs,
  Input,
  Button,
  Form,
  InputNumber,
  Space,
  message,
  Slider,
  Descriptions,
  Drawer,
  Progress,
  Tooltip,
  Alert,
} from 'antd';
import {
  SwapOutlined,
  SearchOutlined,
  ReloadOutlined,
  SettingOutlined,
  SortAscendingOutlined,
  ThunderboltOutlined,
  SaveOutlined,
} from '@ant-design/icons';
import {
  useHybridSearch,
  useUpdateHybridSearchConfig,
} from '../hooks/useMemoryQueries';
import { hybridSearchApi } from '../services/api';

interface Weights {
  alpha: number;
  beta: number;
  gamma: number;
  delta: number;
}

const DEFAULT_WEIGHTS: Weights = { alpha: 0.4, beta: 0.2, gamma: 0.2, delta: 0.2 };

const PRESETS: { label: string; description: string; weights: Weights }[] = [
  {
    label: '语义优先',
    description: '更依赖向量语义相似度',
    weights: { alpha: 0.6, beta: 0.2, gamma: 0.1, delta: 0.1 },
  },
  {
    label: '关键词优先',
    description: '更依赖 BM25 关键词匹配',
    weights: { alpha: 0.2, beta: 0.6, gamma: 0.1, delta: 0.1 },
  },
  {
    label: '均衡模式',
    description: '语义与关键词并重',
    weights: { alpha: 0.3, beta: 0.3, gamma: 0.2, delta: 0.2 },
  },
  {
    label: '时间敏感',
    description: '更强调近期记忆',
    weights: { alpha: 0.2, beta: 0.2, gamma: 0.1, delta: 0.5 },
  },
];

const WEIGHT_META: { key: keyof Weights; label: string; color: string }[] = [
  { key: 'alpha', label: '语义 (α)', color: '#1677ff' },
  { key: 'beta', label: 'BM25 (β)', color: '#52c41a' },
  { key: 'gamma', label: '实体 (γ)', color: '#faad14' },
  { key: 'delta', label: '时间 (δ)', color: '#eb2f96' },
];

export default function HybridSearchPage() {
  // Hybrid search state
  const [query, setQuery] = useState('');
  const [weights, setWeights] = useState<Weights>(DEFAULT_WEIGHTS);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const searchMut = useHybridSearch();
  const updateConfigMut = useUpdateHybridSearchConfig();

  const searchResult = searchMut.data;
  const searchLoading = searchMut.isPending;

  // Debounced auto search when query or weights change
  useEffect(() => {
    const timer = setTimeout(() => {
      if (query.trim()) {
        searchMut.mutate({ query, ...weights, top_k: 10 });
      }
    }, 500);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, weights.alpha, weights.beta, weights.gamma, weights.delta]);

  const handleSearch = () => {
    if (!query.trim()) return;
    searchMut.mutate({ query, ...weights, top_k: 10 });
  };

  const handleSaveConfig = () => {
    updateConfigMut.mutate(
      { ...weights },
      {
        onSuccess: () => message.success('当前权重配置已保存'),
        onError: () => message.error('保存配置失败'),
      }
    );
  };

  const applyPreset = (presetWeights: Weights) => {
    setWeights(presetWeights);
    message.success('已应用预设方案');
  };

  const resetWeights = () => {
    setWeights(DEFAULT_WEIGHTS);
    message.info('已恢复默认权重');
  };

  const setWeight = (key: keyof Weights, value: number) => {
    setWeights(prev => ({ ...prev, [key]: value }));
  };

  const weightSum = useMemo(
    () => weights.alpha + weights.beta + weights.gamma + weights.delta,
    [weights]
  );

  // BM25 state
  const [bm25Query, setBm25Query] = useState('');
  const [bm25Result, setBm25Result] = useState<any>(null);
  const [bm25Loading, setBm25Loading] = useState(false);

  // Rerank state
  const [rerankQuery, setRerankQuery] = useState('');
  const [rerankInput, setRerankInput] = useState('');
  const [rerankResult, setRerankResult] = useState<any>(null);
  const [rerankLoading, setRerankLoading] = useState(false);

  // Config state
  const [config, setConfig] = useState<Record<string, any> | null>(null);
  const [configLoading, setConfigLoading] = useState(false);

  const handleBm25 = async () => {
    if (!bm25Query.trim()) return;
    setBm25Loading(true);
    try {
      const res = await hybridSearchApi.bm25({ query: bm25Query, top_k: 10 });
      setBm25Result(res.data);
    } catch {
      message.error('BM25 搜索失败');
    }
    setBm25Loading(false);
  };

  const handleRerank = async () => {
    if (!rerankQuery.trim() || !rerankInput.trim()) return;
    setRerankLoading(true);
    try {
      let fragments: { id: string; content: string }[];
      try {
        fragments = JSON.parse(rerankInput);
      } catch {
        message.error('片段列表格式错误，需为 JSON 数组');
        setRerankLoading(false);
        return;
      }
      const res = await hybridSearchApi.rerank({ query: rerankQuery, fragments });
      setRerankResult(res.data);
    } catch {
      message.error('重排序失败');
    }
    setRerankLoading(false);
  };

  const fetchConfig = async () => {
    setConfigLoading(true);
    try {
      const res = await hybridSearchApi.config();
      setConfig(res.data);
    } catch {
      message.error('获取配置失败');
    }
    setConfigLoading(false);
  };

  const handleSaveServerConfig = async (values: Record<string, any>) => {
    try {
      await hybridSearchApi.updateConfig(values);
      message.success('配置已更新');
      fetchConfig();
    } catch {
      message.error('更新失败');
    }
  };

  const fragments = (() => {
    if (!searchResult) return [];
    const raw = searchResult.fragments || searchResult.results || searchResult || [];
    return Array.isArray(raw) ? raw : [];
  })();

  const bm25Fragments = (() => {
    if (!bm25Result) return [];
    const raw = bm25Result.fragments || bm25Result.results || bm25Result || [];
    return Array.isArray(raw) ? raw : [];
  })();

  const renderSignalBreakdown = (record: any) => {
    const breakdown = record._signal_breakdown || {};
    const signals = [
      { label: '语义', value: breakdown.semantic ?? record.semantic_score ?? 0, color: '#1677ff' },
      { label: 'BM25', value: breakdown.bm25 ?? record.bm25_score ?? 0, color: '#52c41a' },
      { label: '实体', value: breakdown.entity ?? record.entity_boost ?? 0, color: '#faad14' },
      { label: '时间', value: breakdown.recency ?? record.recency_score ?? 0, color: '#eb2f96' },
    ];
    return (
      <Space direction="vertical" size={2} style={{ width: 180 }}>
        {signals.map(s => (
          <div key={s.label} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ width: 36, fontSize: 12, color: '#666' }}>{s.label}</span>
            <Progress
              percent={Math.round((s.value || 0) * 100)}
              size="small"
              strokeColor={s.color}
              showInfo={false}
              style={{ flex: 1, margin: 0 }}
            />
            <span style={{ width: 36, fontSize: 12, textAlign: 'right' }}>
              {(s.value || 0).toFixed(2)}
            </span>
          </div>
        ))}
      </Space>
    );
  };

  return (
    <div>
      <div className="page-header">
        <h2>
          <SwapOutlined /> 混合搜索
        </h2>
        <p>多信号融合检索 — 语义 + BM25 + 图谱 + 重排序</p>
      </div>

      <Tabs
        defaultActiveKey="search"
        items={[
          // ======== Tab 1: Hybrid Search ========
          {
            key: 'search',
            label: (
              <span>
                <SearchOutlined /> 混合搜索
              </span>
            ),
            children: (
              <Row gutter={16}>
                <Col span={24} style={{ marginBottom: 16 }}>
                  <Card>
                    <Space.Compact style={{ width: '100%' }}>
                      <Input
                        value={query}
                        onChange={e => setQuery(e.target.value)}
                        placeholder="输入搜索查询..."
                        onPressEnter={handleSearch}
                        size="large"
                      />
                      <Button
                        type="primary"
                        icon={<SearchOutlined />}
                        loading={searchLoading}
                        onClick={handleSearch}
                        size="large"
                      >
                        搜索
                      </Button>
                      <Tooltip title="权重调优">
                        <Button
                          icon={<SettingOutlined />}
                          size="large"
                          onClick={() => setDrawerOpen(true)}
                        />
                      </Tooltip>
                    </Space.Compact>
                    <div style={{ marginTop: 12, display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                      {WEIGHT_META.map(meta => (
                        <Tag key={meta.key} color={meta.color}>
                          {meta.label}: {weights[meta.key].toFixed(2)}
                        </Tag>
                      ))}
                      <Tag color={Math.abs(weightSum - 1) < 0.001 ? 'success' : 'warning'}>
                        权重和: {weightSum.toFixed(2)}
                      </Tag>
                    </div>
                  </Card>
                </Col>
                <Col span={24}>
                  <Card title="搜索结果" size="small">
                    {fragments.length > 0 ? (
                      <Table
                        dataSource={fragments}
                        rowKey={(r, i) => r.id || i}
                        size="small"
                        pagination={false}
                        scroll={{ x: 'max-content' }}
                        columns={[
                          { title: '#', width: 40, render: (_: any, __: any, i: number) => i + 1 },
                          { title: '内容', dataIndex: 'content', ellipsis: true, width: 280 },
                          {
                            title: '类型',
                            dataIndex: 'fragment_type',
                            width: 80,
                            render: (t: string) => <Tag>{t || '-'}</Tag>,
                          },
                          {
                            title: '综合分',
                            width: 90,
                            render: (_: any, r: any) => (
                              <Tag color="blue">
                                {(r.final_score ?? r._fusion_score ?? r.score ?? r.similarity ?? 0).toFixed(3)}
                              </Tag>
                            ),
                          },
                          {
                            title: '信号得分分解',
                            width: 220,
                            render: (_: any, r: any) => renderSignalBreakdown(r),
                          },
                          {
                            title: '语义分',
                            dataIndex: 'semantic_score',
                            width: 70,
                            render: (v: any) => v?.toFixed(3) || '-',
                          },
                          {
                            title: 'BM25分',
                            dataIndex: 'bm25_score',
                            width: 70,
                            render: (v: any) => v?.toFixed(3) || '-',
                          },
                          {
                            title: '实体加分',
                            dataIndex: 'entity_boost',
                            width: 75,
                            render: (v: any) => v?.toFixed(3) || '-',
                          },
                          {
                            title: '时间分',
                            dataIndex: 'recency_score',
                            width: 70,
                            render: (v: any) => v?.toFixed(3) || '-',
                          },
                        ]}
                      />
                    ) : (
                      <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>
                        输入查询并点击搜索
                      </div>
                    )}
                  </Card>
                </Col>
              </Row>
            ),
          },

          // ======== Tab 2: BM25 Search ========
          {
            key: 'bm25',
            label: (
              <span>
                <ThunderboltOutlined /> BM25 搜索
              </span>
            ),
            children: (
              <Card
                title="BM25 全文检索"
                extra={
                  <Button
                    icon={<ReloadOutlined />}
                    onClick={() => {
                      setBm25Result(null);
                      setBm25Query('');
                    }}
                  >
                    重置
                  </Button>
                }
              >
                <Space.Compact style={{ width: '100%', marginBottom: 16 }}>
                  <Input
                    value={bm25Query}
                    onChange={e => setBm25Query(e.target.value)}
                    placeholder="输入关键词进行 BM25 全文搜索..."
                    onPressEnter={handleBm25}
                  />
                  <Button
                    type="primary"
                    icon={<SearchOutlined />}
                    loading={bm25Loading}
                    onClick={handleBm25}
                  >
                    搜索
                  </Button>
                </Space.Compact>
                {bm25Fragments.length > 0 ? (
                  <Table
                    dataSource={bm25Fragments}
                    rowKey={(r, i) => r.id || i}
                    size="small"
                    pagination={false}
                    columns={[
                      { title: '#', width: 40, render: (_: any, __: any, i: number) => i + 1 },
                      { title: '内容', dataIndex: 'content', ellipsis: true },
                      {
                        title: 'BM25 分数',
                        width: 100,
                        render: (_: any, r: any) => (r.bm25_score ?? r.score ?? 0).toFixed(4),
                      },
                      {
                        title: '类型',
                        dataIndex: 'fragment_type',
                        width: 80,
                        render: (t: string) => <Tag>{t || '-'}</Tag>,
                      },
                    ]}
                  />
                ) : (
                  <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>
                    输入关键词进行 BM25 全文搜索
                  </div>
                )}
              </Card>
            ),
          },

          // ======== Tab 3: Rerank ========
          {
            key: 'rerank',
            label: (
              <span>
                <SortAscendingOutlined /> 重排序
              </span>
            ),
            children: (
              <Row gutter={16}>
                <Col span={12}>
                  <Card title="重排序输入">
                    <div style={{ marginBottom: 12 }}>
                      <div style={{ marginBottom: 4 }}>查询文本：</div>
                      <Input
                        value={rerankQuery}
                        onChange={e => setRerankQuery(e.target.value)}
                        placeholder="输入查询..."
                      />
                    </div>
                    <div style={{ marginBottom: 12 }}>
                      <div style={{ marginBottom: 4 }}>片段列表 (JSON)：</div>
                      <Input.TextArea
                        rows={8}
                        value={rerankInput}
                        onChange={e => setRerankInput(e.target.value)}
                        placeholder='[{"id": "1", "content": "..."}, {"id": "2", "content": "..."}]'
                      />
                    </div>
                    <Button
                      type="primary"
                      icon={<SortAscendingOutlined />}
                      loading={rerankLoading}
                      onClick={handleRerank}
                    >
                      重排序
                    </Button>
                  </Card>
                </Col>
                <Col span={12}>
                  <Card title="排序结果">
                    {rerankResult ? (
                      <pre
                        className="code-block"
                        style={{ maxHeight: 400, overflow: 'auto' }}
                      >
                        {JSON.stringify(rerankResult, null, 2)}
                      </pre>
                    ) : (
                      <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>
                        输入查询和片段列表后点击重排序
                      </div>
                    )}
                  </Card>
                </Col>
              </Row>
            ),
          },

          // ======== Tab 4: Config ========
          {
            key: 'config',
            label: (
              <span>
                <SettingOutlined /> 配置
              </span>
            ),
            children: (
              <Row gutter={16}>
                <Col span={12}>
                  <Card
                    title="当前配置"
                    extra={
                      <Button
                        icon={<ReloadOutlined />}
                        onClick={fetchConfig}
                        loading={configLoading}
                      >
                        加载
                      </Button>
                    }
                  >
                    {config ? (
                      <Descriptions size="small" column={1} bordered>
                        {Object.entries(config).map(([key, value]) => (
                          <Descriptions.Item label={key} key={key}>
                            {String(value)}
                          </Descriptions.Item>
                        ))}
                      </Descriptions>
                    ) : (
                      <div style={{ textAlign: 'center', padding: 30, color: '#999' }}>
                        点击加载配置
                      </div>
                    )}
                  </Card>
                </Col>
                <Col span={12}>
                  <Card title="更新配置">
                    <Form layout="vertical" onFinish={handleSaveServerConfig}>
                      <Form.Item name="alpha" label="语义权重 (alpha)">
                        <InputNumber min={0} max={1} step={0.1} style={{ width: '100%' }} />
                      </Form.Item>
                      <Form.Item name="beta" label="BM25 权重 (beta)">
                        <InputNumber min={0} max={1} step={0.1} style={{ width: '100%' }} />
                      </Form.Item>
                      <Form.Item name="gamma" label="图谱权重 (gamma)">
                        <InputNumber min={0} max={1} step={0.1} style={{ width: '100%' }} />
                      </Form.Item>
                      <Form.Item name="delta" label="时间权重 (delta)">
                        <InputNumber min={0} max={1} step={0.1} style={{ width: '100%' }} />
                      </Form.Item>
                      <Button type="primary" htmlType="submit">
                        保存配置
                      </Button>
                      <Button
                        style={{ marginLeft: 8 }}
                        onClick={async () => {
                          try {
                            await hybridSearchApi.rebuildIndex();
                            message.success('索引重建已触发');
                          } catch {
                            message.error('重建失败');
                          }
                        }}
                      >
                        重建索引
                      </Button>
                    </Form>
                  </Card>
                </Col>
              </Row>
            ),
          },
        ]}
      />

      {/* Weight tuning drawer */}
      <Drawer
        title="混合搜索权重调优"
        placement="right"
        width={420}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        extra={
          <Space>
            <Button onClick={resetWeights}>恢复默认</Button>
            <Button
              type="primary"
              icon={<SaveOutlined />}
              loading={updateConfigMut.isPending}
              onClick={handleSaveConfig}
            >
              保存当前配置
            </Button>
          </Space>
        }
      >
        <Space direction="vertical" size="large" style={{ width: '100%' }}>
          <Alert
            message="权重提示"
            description="四个权重之和建议为 1.0；系统不会强制归一化，但未归一的权重会改变最终得分的绝对尺度。"
            type="info"
            showIcon
          />

          <Card size="small" title="当前权重">
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
              <span>权重总和</span>
              <Tag color={Math.abs(weightSum - 1) < 0.001 ? 'success' : 'warning'}>
                {weightSum.toFixed(2)}
              </Tag>
            </div>
            {Math.abs(weightSum - 1) >= 0.001 && (
              <Alert
                message={`当前权重和为 ${weightSum.toFixed(2)}，建议调整至 1.00`}
                type="warning"
                showIcon
                style={{ marginBottom: 12 }}
              />
            )}
            {WEIGHT_META.map(meta => (
              <div key={meta.key} style={{ marginBottom: 16 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                  <span style={{ color: meta.color, fontWeight: 500 }}>{meta.label}</span>
                  <span>{weights[meta.key].toFixed(2)}</span>
                </div>
                <Slider
                  min={0}
                  max={1}
                  step={0.05}
                  value={weights[meta.key]}
                  onChange={v => setWeight(meta.key, v)}
                  trackStyle={{ backgroundColor: meta.color }}
                  handleStyle={{ borderColor: meta.color }}
                />
              </div>
            ))}
          </Card>

          <Card size="small" title="预设方案">
            <Space direction="vertical" style={{ width: '100%' }}>
              {PRESETS.map(preset => (
                <Card
                  key={preset.label}
                  size="small"
                  hoverable
                  onClick={() => applyPreset(preset.weights)}
                  style={{ cursor: 'pointer' }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <div>
                      <div style={{ fontWeight: 500 }}>{preset.label}</div>
                      <div style={{ fontSize: 12, color: '#888' }}>{preset.description}</div>
                    </div>
                    <Space size={4}>
                      {WEIGHT_META.map(meta => (
                        <Tag key={meta.key} color={meta.color} style={{ fontSize: 11 }}>
                          {meta.label.split(' ')[0]} {preset.weights[meta.key].toFixed(1)}
                        </Tag>
                      ))}
                    </Space>
                  </div>
                </Card>
              ))}
            </Space>
          </Card>

          <Card size="small" title="实时预览">
            <div style={{ color: '#666', fontSize: 13 }}>
              调整滑块后，若已有查询条件，系统会在 500ms 后自动重新搜索并展示新的综合得分。
            </div>
          </Card>
        </Space>
      </Drawer>
    </div>
  );
}
