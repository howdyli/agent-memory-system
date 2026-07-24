import { useState, useCallback, useMemo } from 'react';
import {
  Card, Row, Col, Table, Tag, Tabs, Input, Button, Form, Space, message,
  Select, Modal, Popconfirm, Statistic, Drawer,
  Descriptions, Segmented, Tooltip, Divider,
} from 'antd';
import {
  ShareAltOutlined, NodeIndexOutlined, LinkOutlined,
  ThunderboltOutlined, PlusOutlined, DeleteOutlined,
  ReloadOutlined, SearchOutlined, EyeOutlined, ApartmentOutlined,
  BarChartOutlined, DownloadOutlined, FilterOutlined, InfoCircleOutlined,
  TeamOutlined, BranchesOutlined,
} from '@ant-design/icons';
import { graphApi } from '../services/api';
import {
  useGraphEntities, useGraphRelationships, useGraphStatistics,
  useCreateEntity, useCreateRelationship,
} from '../hooks/useMemoryQueries';
import GraphVisualizer from '../components/GraphVisualizer';

const ENTITY_TYPES = ['person', 'organization', 'location', 'event', 'concept', 'other'];

const ENTITY_COLORS: Record<string, string> = {
  person: '#1677ff', organization: '#52c41a', location: '#faad14',
  event: '#ff4d4f', concept: '#722ed1', technology: '#eb2f96',
  product: '#fa8c16', other: '#13c2c2',
};

type LayoutMode = 'force' | 'hierarchical' | 'static';

export default function GraphMemoryPage() {
  // ---- Data ----
  const [entityQuery, setEntityQuery] = useState<string | undefined>(undefined);
  const { data: entities = [], isLoading: entitiesLoading, refetch: refetchEntities } = useGraphEntities(entityQuery);
  const [relEntityId] = useState<string | undefined>(undefined);
  const { data: relationships = [], isLoading: relsLoading, refetch: refetchRelationships } = useGraphRelationships(relEntityId);
  const { data: stats } = useGraphStatistics();
  const createEntity = useCreateEntity();
  const createRelationship = useCreateRelationship();

  // ---- UI state ----
  const [neighbors, setNeighbors] = useState<any[]>([]);
  const [loading, setLoading] = useState<Record<string, boolean>>({});
  const [entityModal, setEntityModal] = useState(false);
  const [entityForm] = Form.useForm();
  const [relModal, setRelModal] = useState(false);
  const [relForm] = Form.useForm();
  const [extractText, setExtractText] = useState('');
  const [extractResult, setExtractResult] = useState<any>(null);
  const [graphQuery, setGraphQuery] = useState('');
  const [graphResult, setGraphResult] = useState<any>(null);
  const [neighborEntityId, setNeighborEntityId] = useState('');

  // ---- Visualizer enhancements ----
  const [vizRefreshKey, setVizRefreshKey] = useState(0);
  const [vizSearch, setVizSearch] = useState('');
  const [layoutMode, setLayoutMode] = useState<LayoutMode>('force');
  const [typeFilter, setTypeFilter] = useState<string[]>([]);
  const [selectedEntity, setSelectedEntity] = useState<any>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const setLoad = (key: string, val: boolean) => setLoading(prev => ({ ...prev, [key]: val }));

  // ---- Filtered entities (by type) ----
  const filteredEntities = useMemo(() => {
    if (typeFilter.length === 0) return entities;
    return entities.filter((e: any) => typeFilter.includes((e.entity_type || 'other').toLowerCase()));
  }, [entities, typeFilter]);

  // ---- Filtered relationships (only those connecting filtered entities) ----
  const filteredRelationships = useMemo(() => {
    if (typeFilter.length === 0) return relationships;
    const ids = new Set(filteredEntities.map((e: any) => String(e.id ?? e.entity_id ?? '')));
    return relationships.filter((r: any) =>
      ids.has(String(r.source_entity_id || '')) || ids.has(String(r.target_entity_id || ''))
    );
  }, [relationships, filteredEntities, typeFilter]);

  // ---- Handlers ----
  const handleCreateEntity = async () => {
    const vals = await entityForm.validateFields();
    try { await createEntity.mutateAsync(vals); message.success('实体已创建'); setEntityModal(false); entityForm.resetFields(); }
    catch { message.error('创建失败'); }
  };

  const handleCreateRelationship = async () => {
    const vals = await relForm.validateFields();
    try { await createRelationship.mutateAsync(vals); message.success('关系已创建'); setRelModal(false); relForm.resetFields(); }
    catch { message.error('创建失败'); }
  };

  const handleExtract = async () => {
    if (!extractText.trim()) return;
    try { const res = await graphApi.extractEntities(extractText); setExtractResult(res.data); message.success('抽取完成'); }
    catch { message.error('抽取失败'); }
  };

  const handleQueryGraph = async () => {
    if (!graphQuery.trim()) return;
    setLoad('graph', true);
    try { const res = await graphApi.queryGraph(graphQuery); setGraphResult(res.data); } catch { message.error('查询失败'); }
    setLoad('graph', false);
  };

  const handleNeighbors = useCallback(async () => {
    if (!neighborEntityId.trim()) return;
    setLoad('neighbors', true);
    try { const res = await graphApi.getNeighbors(neighborEntityId.trim()); setNeighbors(res.data?.neighbors || res.data || []); } catch { message.error('获取邻居失败'); }
    setLoad('neighbors', false);
  }, [neighborEntityId]);

  const handleDeactivateRel = async (id: string) => {
    try { await graphApi.deactivateRelationship(id); message.success('关系已停用'); refetchRelationships(); } catch { message.error('操作失败'); }
  };

  const refreshVisualizer = useCallback(() => {
    refetchEntities(); refetchRelationships(); setVizRefreshKey(k => k + 1);
  }, [refetchEntities, refetchRelationships]);

  const handleNodeSelect = useCallback((entity: any | null) => {
    if (entity) {
      setSelectedEntity(entity);
      setDrawerOpen(true);
    } else {
      setDrawerOpen(false);
    }
  }, []);

  // Export graph as JSON
  const handleExport = useCallback(() => {
    const data = {
      entities: filteredEntities,
      relationships: filteredRelationships,
      exported_at: new Date().toISOString(),
    };
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `graph-export-${Date.now()}.json`; a.click();
    URL.revokeObjectURL(url);
    message.success('导出成功');
  }, [filteredEntities, filteredRelationships]);

  // Entity type distribution for stats
  const typeDistribution = useMemo(() => {
    const map: Record<string, number> = {};
    entities.forEach((e: any) => {
      const t = (e.entity_type || 'other').toLowerCase();
      map[t] = (map[t] || 0) + 1;
    });
    return Object.entries(map).sort((a, b) => b[1] - a[1]);
  }, [entities]);

  return (
    <div>
      <div className="page-header">
        <h2><ShareAltOutlined /> 知识图谱</h2>
        <p>实体与关系管理、图谱可视化与智能查询</p>
      </div>

      {/* Statistics Cards */}
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="实体总数" value={stats?.entity_count ?? entities.length}
              prefix={<TeamOutlined style={{ color: '#1677ff' }} />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="关系总数" value={stats?.relationship_count ?? relationships.length}
              prefix={<BranchesOutlined style={{ color: '#52c41a' }} />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="实体类型" value={stats?.entity_types ? Object.keys(stats.entity_types).length : typeDistribution.length}
              prefix={<BarChartOutlined style={{ color: '#722ed1' }} />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="类型分布"
              value={typeDistribution.length > 0 ? typeDistribution[0][0] : '-'}
              suffix={typeDistribution.length > 0 ? <span style={{ fontSize: 12, color: '#999' }}>({typeDistribution[0][1]})</span> : null}
              prefix={<FilterOutlined style={{ color: '#fa8c16' }} />}
            />
          </Card>
        </Col>
      </Row>

      <Tabs defaultActiveKey="visualizer" items={[
        // ======== Tab 1: Visualization ========
        {
          key: 'visualizer', label: <span><ApartmentOutlined /> 图谱可视化</span>,
          children: (
            <Card
              title={
                <Space>
                  <span>力导向图</span>
                  <Segmented
                    size="small"
                    value={layoutMode}
                    onChange={v => setLayoutMode(v as LayoutMode)}
                    options={[
                      { label: '力导向', value: 'force' },
                      { label: '层次', value: 'hierarchical' },
                      { label: '静态', value: 'static' },
                    ]}
                  />
                </Space>
              }
              extra={
                <Space>
                  <Input
                    placeholder="搜索实体..."
                    prefix={<SearchOutlined />}
                    size="small"
                    style={{ width: 160 }}
                    allowClear
                    value={vizSearch}
                    onChange={e => setVizSearch(e.target.value)}
                  />
                  <Select
                    mode="multiple"
                    placeholder="类型过滤"
                    size="small"
                    style={{ minWidth: 140 }}
                    allowClear
                    maxTagCount={2}
                    value={typeFilter}
                    onChange={setTypeFilter}
                    options={ENTITY_TYPES.map(t => ({ value: t, label: t }))}
                  />
                  <Tooltip title="导出 JSON">
                    <Button size="small" icon={<DownloadOutlined />} onClick={handleExport}>导出</Button>
                  </Tooltip>
                  <Button size="small" icon={<ReloadOutlined />} onClick={refreshVisualizer}>刷新</Button>
                </Space>
              }
            >
              <GraphVisualizer
                key={vizRefreshKey}
                entities={filteredEntities}
                relationships={filteredRelationships}
                height={540}
                searchKeyword={vizSearch}
                onNodeSelect={handleNodeSelect}
                layoutMode={layoutMode}
              />
            </Card>
          ),
        },

        // ======== Tab 2: Entities ========
        {
          key: 'entities', label: <span><NodeIndexOutlined /> 实体管理</span>,
          children: (
            <Card title="实体列表"
              extra={
                <Space>
                  <Input.Search placeholder="搜索实体..." onSearch={v => setEntityQuery(v || undefined)} style={{ width: 200 }} />
                  <Button type="primary" icon={<PlusOutlined />} onClick={() => { entityForm.resetFields(); setEntityModal(true); }}>新建实体</Button>
                </Space>
              }>
              <Table dataSource={entities} rowKey={(r) => r.id || r.entity_id || Math.random()} size="small" loading={entitiesLoading}
                columns={[
                  { title: '名称', dataIndex: 'name', ellipsis: true },
                  {
                    title: '类型', dataIndex: 'entity_type', width: 100,
                    render: (t: string) => <Tag color={ENTITY_COLORS[t?.toLowerCase()] || '#8c8c8c'}>{t}</Tag>,
                  },
                  { title: '置信度', dataIndex: 'confidence', width: 80, render: (v: number) => v?.toFixed(2) || '-' },
                  { title: '关联数', width: 70, render: (_v: any, r: any) => r.relation_count ?? r.relationship_count ?? '-' },
                  { title: '创建时间', dataIndex: 'created_at', width: 130, render: (v: string) => v ? new Date(v).toLocaleString() : '-' },
                  {
                    title: '操作', width: 120, render: (_: any, r: any) => (
                      <Space>
                        <Button size="small" icon={<EyeOutlined />} onClick={() => {
                          setNeighborEntityId(r.id || r.entity_id); handleNeighbors();
                        }}>邻居</Button>
                        <Button size="small" icon={<InfoCircleOutlined />} onClick={() => {
                          setSelectedEntity(r); setDrawerOpen(true);
                        }}>详情</Button>
                      </Space>
                    )
                  },
                ]} />
            </Card>
          ),
        },

        // ======== Tab 3: Relationships ========
        {
          key: 'relationships', label: <span><LinkOutlined /> 关系管理</span>,
          children: (
            <Card title="关系列表"
              extra={
                <Space>
                  <Button type="primary" icon={<PlusOutlined />} onClick={() => { relForm.resetFields(); setRelModal(true); }}>新建关系</Button>
                  <Button icon={<ReloadOutlined />} onClick={() => refetchRelationships()}>刷新</Button>
                </Space>
              }>
              <Table dataSource={relationships} rowKey={(r) => r.id || r.relationship_id || Math.random()} size="small" loading={relsLoading}
                columns={[
                  { title: '源实体', ellipsis: true, render: (_: any, r: any) => r.source_entity_name || r.source_entity_id || '-' },
                  { title: '关系', dataIndex: 'relation_type', width: 100, render: (t: string) => <Tag color="purple">{t}</Tag> },
                  { title: '目标实体', ellipsis: true, render: (_: any, r: any) => r.target_entity_name || r.target_entity_id || '-' },
                  { title: '权重', width: 70, render: (_: any, r: any) => { const v = r.weight ?? r.confidence; return v != null ? Number(v).toFixed(2) : '-'; } },
                  { title: '状态', dataIndex: 'status', width: 70, render: (s: string) => <Tag color={s === 'active' ? 'green' : 'red'}>{s || 'active'}</Tag> },
                  { title: '创建时间', dataIndex: 'created_at', width: 130, render: (v: string) => v ? new Date(v).toLocaleString() : '-' },
                  {
                    title: '操作', width: 80, render: (_: any, r: any) => (
                      <Popconfirm title="停用此关系？" onConfirm={() => handleDeactivateRel(r.id || r.relationship_id)}>
                        <Button size="small" danger icon={<DeleteOutlined />}>停用</Button>
                      </Popconfirm>
                    )
                  },
                ]} />
            </Card>
          ),
        },

        // ======== Tab 4: Entity Extraction ========
        {
          key: 'extract', label: <span><ThunderboltOutlined /> 实体抽取</span>,
          children: (
            <Row gutter={16}>
              <Col span={12}>
                <Card title="从文本抽取实体">
                  <Input.TextArea rows={6} value={extractText} onChange={e => setExtractText(e.target.value)} placeholder="输入文本内容，自动抽取实体和关系..." />
                  <div style={{ marginTop: 12 }}>
                    <Button icon={<ThunderboltOutlined />} onClick={handleExtract} loading={loading.extract}>抽取</Button>
                  </div>
                </Card>
              </Col>
              <Col span={12}>
                <Card title="抽取结果">
                  {extractResult ? (
                    <pre className="code-block" style={{ maxHeight: 400, overflow: 'auto' }}>{JSON.stringify(extractResult, null, 2)}</pre>
                  ) : <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>点击抽取查看结果</div>}
                </Card>
              </Col>
            </Row>
          ),
        },

        // ======== Tab 5: Graph Browser ========
        {
          key: 'browser', label: <span><ShareAltOutlined /> 图谱浏览</span>,
          children: (
            <Row gutter={16}>
              <Col span={24} style={{ marginBottom: 16 }}>
                <Card title="图谱查询">
                  <Space.Compact style={{ width: '100%' }}>
                    <Input value={graphQuery} onChange={e => setGraphQuery(e.target.value)} placeholder="输入查询..." onPressEnter={handleQueryGraph} />
                    <Button type="primary" icon={<SearchOutlined />} loading={loading.graph} onClick={handleQueryGraph}>查询</Button>
                  </Space.Compact>
                  {graphResult && (
                    <div style={{ marginTop: 12 }}>
                      <pre className="code-block" style={{ maxHeight: 300, overflow: 'auto' }}>{JSON.stringify(graphResult, null, 2)}</pre>
                    </div>
                  )}
                </Card>
              </Col>
              <Col span={24}>
                <Card title="邻居查询"
                  extra={
                    <Space>
                      <Input value={neighborEntityId} onChange={e => setNeighborEntityId(e.target.value)} placeholder="实体 ID..." style={{ width: 200 }} />
                      <Button type="primary" onClick={handleNeighbors} loading={loading.neighbors}>查询邻居</Button>
                    </Space>
                  }>
                  {neighbors.length > 0 ? (
                    <Table dataSource={neighbors} rowKey={(r, i) => r.entity_id || i} size="small" pagination={false}
                      columns={[
                        { title: '实体名称', ellipsis: true, render: (_: any, r: any) => r.entity_name || r.name || '-' },
                        { title: '关系', dataIndex: 'relation_type', width: 100, render: (t: string) => <Tag color="purple">{t}</Tag> },
                        { title: '置信度', dataIndex: 'confidence', width: 80, render: (v: number) => v?.toFixed(2) || '-' },
                      ]} />
                  ) : <div style={{ textAlign: 'center', padding: 30, color: '#999' }}>输入实体 ID 查询邻居关系</div>}
                </Card>
              </Col>
            </Row>
          ),
        },
      ]} />

      {/* Entity Detail Drawer */}
      <Drawer
        title={
          <Space>
            <span style={{
              width: 12, height: 12, borderRadius: '50%',
              background: ENTITY_COLORS[(selectedEntity?.entity_type || 'other').toLowerCase()] || '#8c8c8c',
              display: 'inline-block',
            }} />
            {selectedEntity?.name || '实体详情'}
          </Space>
        }
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        width={400}
      >
        {selectedEntity && (
          <>
            <Descriptions column={1} bordered size="small">
              <Descriptions.Item label="ID">{selectedEntity.id || selectedEntity.entity_id || '-'}</Descriptions.Item>
              <Descriptions.Item label="名称">{selectedEntity.name || '-'}</Descriptions.Item>
              <Descriptions.Item label="类型">
                <Tag color={ENTITY_COLORS[(selectedEntity.entity_type || 'other').toLowerCase()] || '#8c8c8c'}>
                  {selectedEntity.entity_type || '-'}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="置信度">{selectedEntity.confidence?.toFixed(2) || '-'}</Descriptions.Item>
              <Descriptions.Item label="关联数">{selectedEntity.relation_count ?? selectedEntity.relationship_count ?? '-'}</Descriptions.Item>
              <Descriptions.Item label="创建时间">
                {selectedEntity.created_at ? new Date(selectedEntity.created_at).toLocaleString() : '-'}
              </Descriptions.Item>
              <Descriptions.Item label="更新时间">
                {selectedEntity.updated_at ? new Date(selectedEntity.updated_at).toLocaleString() : '-'}
              </Descriptions.Item>
            </Descriptions>

            {selectedEntity.properties && (
              <>
                <Divider style={{ fontSize: 13 }}>属性</Divider>
                <pre className="code-block" style={{ maxHeight: 200, overflow: 'auto', fontSize: 12 }}>
                  {typeof selectedEntity.properties === 'string'
                    ? selectedEntity.properties
                    : JSON.stringify(selectedEntity.properties, null, 2)}
                </pre>
              </>
            )}

            <Divider style={{ fontSize: 13 }}>快速操作</Divider>
            <Space direction="vertical" style={{ width: '100%' }}>
              <Button
                block icon={<SearchOutlined />}
                onClick={() => {
                  setNeighborEntityId(String(selectedEntity.id || selectedEntity.entity_id));
                  handleNeighbors();
                  setDrawerOpen(false);
                }}
              >
                查询邻居关系
              </Button>
              <Button
                block icon={<ApartmentOutlined />}
                onClick={() => {
                  setVizSearch(selectedEntity.name || '');
                  setDrawerOpen(false);
                }}
              >
                在图谱中高亮
              </Button>
            </Space>
          </>
        )}
      </Drawer>

      {/* Create Entity Modal */}
      <Modal title="新建实体" open={entityModal} onOk={handleCreateEntity} onCancel={() => setEntityModal(false)}>
        <Form form={entityForm} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="entity_type" label="类型" rules={[{ required: true }]}>
            <Select options={ENTITY_TYPES.map(t => ({ value: t, label: t }))} />
          </Form.Item>
          <Form.Item name="properties" label="属性 (JSON)">
            <Input.TextArea rows={3} placeholder='{"key": "value"}' />
          </Form.Item>
        </Form>
      </Modal>

      {/* Create Relationship Modal */}
      <Modal title="新建关系" open={relModal} onOk={handleCreateRelationship} onCancel={() => setRelModal(false)}>
        <Form form={relForm} layout="vertical">
          <Form.Item name="source_entity_id" label="源实体 ID" rules={[{ required: true }]}><Input placeholder="输入源实体 ID" /></Form.Item>
          <Form.Item name="relation_type" label="关系类型" rules={[{ required: true }]}><Input placeholder="如: works_at, knows, located_in" /></Form.Item>
          <Form.Item name="target_entity_id" label="目标实体 ID" rules={[{ required: true }]}><Input placeholder="输入目标实体 ID" /></Form.Item>
          <Form.Item name="properties" label="属性 (JSON)">
            <Input.TextArea rows={3} placeholder='{"key": "value"}' />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
