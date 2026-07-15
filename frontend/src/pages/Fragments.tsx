import { useMemo, useState } from 'react';
import { Card, Table, Button, Modal, Form, Input, InputNumber, Select, Space, Popconfirm, message, Tag, Tabs } from 'antd';
import { PlusOutlined, DeleteOutlined, ReloadOutlined, SearchOutlined, ClearOutlined, ThunderboltOutlined, InboxOutlined } from '@ant-design/icons';
import { fragmentsApi, lifecycleApi } from '../services/api';
import { useFragments, useCreateFragment, useUpdateFragment, useDeleteFragment, useBatchDeleteFragments, useSemanticSearch } from '../hooks/useMemoryQueries';
import AdvancedFilter, { filterRecords, type FilterCondition, type FilterField, type FilterLogic } from '../components/AdvancedFilter';
import BatchOperationBar, { type BatchAction } from '../components/BatchOperationBar';

const FRAGMENT_TYPES = ['user_info', 'preference', 'plan', 'key_fact', 'summary', 'custom'];

export default function FragmentsPage() {
  const { data: fragments = [], isLoading, refetch } = useFragments();
  const createFrag = useCreateFragment();
  const updateFrag = useUpdateFragment();
  const deleteFrag = useDeleteFragment();
  const semanticSearch = useSemanticSearch();

  const [createModal, setCreateModal] = useState(false);
  const [editModal, setEditModal] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResult, setSearchResult] = useState<any>(null);
  const [analyzeText, setAnalyzeText] = useState('');
  const [analyzeResult, setAnalyzeResult] = useState<any>(null);
  const [prompts, setPrompts] = useState<Record<string, unknown>[]>([]);
  const [promptModal, setPromptModal] = useState(false);
  const [filterConditions, setFilterConditions] = useState<FilterCondition[]>([]);
  const [filterLogic, setFilterLogic] = useState<FilterLogic>('AND');
  const [form] = Form.useForm();
  const [editForm] = Form.useForm();
  const [promptForm] = Form.useForm();

  // 批量操作状态
  const [selectedFragmentIds, setSelectedFragmentIds] = useState<number[]>([]);
  const batchDeleteFragments = useBatchDeleteFragments();

  const fetchPrompts = async () => {
    try { const res = await fragmentsApi.listPrompts(); setPrompts(res.data?.prompts || res.data || []); }
    catch { /* */ }
  };

  const handleCreate = async () => {
    const vals = await form.validateFields();
    try { await createFrag.mutateAsync({ ...vals, fragment_type: vals.type }); message.success('片段已创建'); setCreateModal(false); form.resetFields(); }
    catch (e: unknown) { message.error((e as Error).message); }
  };

  const handleEdit = async () => {
    if (!editingId) return;
    const vals = await editForm.validateFields();
    try { await updateFrag.mutateAsync({ id: editingId, ...vals }); message.success('已更新'); setEditModal(false); }
    catch (e: unknown) { message.error((e as Error).message); }
  };

  const handleDelete = async (id: number) => {
    try { await deleteFrag.mutateAsync(id); message.success('已删除'); }
    catch { message.error('删除失败'); }
  };

  const handleBatchDeleteFragments = () => {
    if (selectedFragmentIds.length === 0) return;
    Modal.confirm({
      title: '确认批量删除',
      content: `将删除 ${selectedFragmentIds.length} 个记忆片段，此操作不可撤销。`,
      okText: '删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: async () => {
        try {
          await batchDeleteFragments.mutateAsync(selectedFragmentIds);
          message.success(`已删除 ${selectedFragmentIds.length} 个片段`);
          setSelectedFragmentIds([]);
        } catch { message.error('批量删除失败'); }
      },
    });
  };

  const handleBatchArchiveFragments = () => {
    if (selectedFragmentIds.length === 0) return;
    Modal.confirm({
      title: '确认批量归档',
      content: `将归档 ${selectedFragmentIds.length} 个记忆片段。`,
      okText: '归档',
      cancelText: '取消',
      onOk: async () => {
        const results = await Promise.allSettled(
          selectedFragmentIds.map((id) => lifecycleApi.archive('fragment', String(id)))
        );
        const successCount = results.filter((r) => r.status === 'fulfilled').length;
        message.success(`已归档 ${successCount} 个片段`);
        setSelectedFragmentIds([]);
        refetch();
      },
    });
  };

  const handleSemanticSearch = async () => {
    if (!searchQuery.trim()) return;
    try {
      const res = await semanticSearch.mutateAsync({ query: searchQuery, topK: 10, threshold: 0.5 });
      setSearchResult(res);
    }
    catch { message.error('搜索失败'); }
  };

  const handleAnalyze = async () => {
    if (!analyzeText.trim()) return;
    try { const res = await fragmentsApi.extract(analyzeText); setAnalyzeResult(res.data); message.success('分析完成'); }
    catch { message.error('分析失败'); }
  };

  const handleCleanup = async () => {
    try { const res = await fragmentsApi.cleanup(); message.success(`清理完成: ${res.data?.deleted || 0} 个过期片段`); refetch(); }
    catch { message.error('清理失败'); }
  };

  const handleCreatePrompt = async () => {
    const vals = await promptForm.validateFields();
    try { await fragmentsApi.createPrompt(vals); message.success('模板已创建'); setPromptModal(false); promptForm.resetFields(); fetchPrompts(); }
    catch (e: unknown) { message.error((e as Error).message); }
  };

  const openEdit = (item: Record<string, unknown>) => {
    setEditingId(item.id as number);
    editForm.setFieldsValue({ content: item.content, importance_score: item.importance_score, ttl: item.ttl });
    setEditModal(true);
  };

  const filteredFragments = useMemo(() => {
    return filterRecords(fragments as Record<string, unknown>[], filterConditions, filterLogic);
  }, [fragments, filterConditions, filterLogic]);

  const fragmentFilterFields: FilterField[] = [
    {
      key: 'fragment_type',
      label: '记忆类型',
      type: 'select',
      options: FRAGMENT_TYPES.map((t) => ({ value: t, label: t })),
    },
    { key: 'created_at', label: '创建时间', type: 'dateRange' },
    { key: 'importance_score', label: '重要度', type: 'number' },
    { key: 'content', label: '内容关键词', type: 'text', placeholder: '关键词' },
  ];

  return (
    <div>
      <div className="page-header">
        <h2>记忆片段</h2>
        <p>带 TTL 过期和语义嵌入的记忆片段管理</p>
      </div>

      <Tabs items={[
        {
          key: 'list', label: '片段列表',
          children: (
            <Card className="section-card" extra={
              <Space>
                <Button icon={<ClearOutlined />} onClick={handleCleanup}>清理过期</Button>
                <Button icon={<ReloadOutlined />} onClick={() => refetch()}>刷新</Button>
                <Button type="primary" icon={<PlusOutlined />} onClick={() => { form.resetFields(); setCreateModal(true); }}>新建片段</Button>
              </Space>
            }>
              <AdvancedFilter
                fields={fragmentFilterFields}
                pageName="fragments"
                onFilter={(conditions, logic) => {
                  setFilterConditions(conditions);
                  setFilterLogic(logic);
                }}
                onReset={() => {
                  setFilterConditions([]);
                  setFilterLogic('AND');
                }}
              />
              <Table
                dataSource={filteredFragments}
                rowKey={(r) => String(r.id || r.fragment_id || Math.random())}
                loading={isLoading}
                size="small"
                columns={[
                  { title: 'ID', dataIndex: 'id', width: 50 },
                  { title: '类型', dataIndex: 'fragment_type', width: 90, render: (t: string) => <Tag>{t}</Tag> },
                  { title: '内容', dataIndex: 'content', ellipsis: true },
                  { title: '重要性', dataIndex: 'importance_score', width: 80, render: (v: number) => v?.toFixed(2) || '-' },
                  { title: 'TTL', dataIndex: 'ttl', width: 70, render: (v: number) => v ? `${v}s` : '-' },
                  { title: '创建时间', dataIndex: 'created_at', width: 130, render: (v: string) => v ? new Date(v).toLocaleString() : '-' },
                  { title: '操作', width: 120, render: (_, r) => (
                    <Space>
                      <Button size="small" onClick={() => openEdit(r)}>编辑</Button>
                      <Popconfirm title="删除？" onConfirm={() => handleDelete(r.id as number)}>
                        <Button size="small" danger icon={<DeleteOutlined />} />
                      </Popconfirm>
                    </Space>
                  )},
                ]}
                locale={{ emptyText: '暂无记忆片段' }}
              />
            </Card>
          ),
        },
        {
          key: 'search', label: '语义搜索',
          children: (
            <Card className="section-card">
              <Space.Compact style={{ width: '100%', marginBottom: 16 }}>
                <Input value={searchQuery} onChange={e => setSearchQuery(e.target.value)} placeholder="输入搜索内容..." onPressEnter={handleSemanticSearch} />
                <Button type="primary" icon={<SearchOutlined />} loading={semanticSearch.isPending} onClick={handleSemanticSearch}>搜索</Button>
              </Space.Compact>
              {searchResult && <pre className="code-block">{JSON.stringify(searchResult, null, 2)}</pre>}
            </Card>
          ),
        },
        {
          key: 'analyze', label: '对话分析',
          children: (
            <Card className="section-card">
              <Input.TextArea rows={5} value={analyzeText} onChange={e => setAnalyzeText(e.target.value)} placeholder="输入对话历史..." />
              <div style={{ marginTop: 12 }}><Button type="primary" icon={<ThunderboltOutlined />} onClick={handleAnalyze}>分析抽取</Button></div>
              {analyzeResult && <pre className="code-block" style={{ marginTop: 16 }}>{JSON.stringify(analyzeResult, null, 2)}</pre>}
            </Card>
          ),
        },
        {
          key: 'prompts', label: '抽取模板',
          children: (
            <Card className="section-card" extra={<Button type="primary" size="small" icon={<PlusOutlined />} onClick={() => { promptForm.resetFields(); setPromptModal(true); fetchPrompts(); }}>新建模板</Button>}>
              <Table
                dataSource={prompts}
                rowKey="id"
                size="small"
                pagination={false}
                columns={[
                  { title: '名称', dataIndex: 'name' },
                  { title: '描述', dataIndex: 'description', ellipsis: true },
                  { title: '模板', dataIndex: 'template', ellipsis: true, render: (t: string) => <code style={{ fontSize: 12 }}>{t?.substring(0, 80)}...</code> },
                ]}
                locale={{ emptyText: '暂无可用的 Prompt 模板，点击右上角新建' }}
              />
            </Card>
          ),
        },
      ]} />

      {/* Create Fragment Modal */}
      <Modal title="新建记忆片段" open={createModal} onOk={handleCreate} onCancel={() => setCreateModal(false)}>
        <Form form={form} layout="vertical">
          <Form.Item name="type" label="类型" rules={[{ required: true }]}>
            <Select options={FRAGMENT_TYPES.map(t => ({ value: t, label: t }))} />
          </Form.Item>
          <Form.Item name="content" label="内容" rules={[{ required: true }]}>
            <Input.TextArea rows={3} />
          </Form.Item>
          <Form.Item name="importance_score" label="重要性 (0-1)">
            <InputNumber min={0} max={1} step={0.1} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="ttl" label="TTL (秒)">
            <InputNumber min={1} placeholder="留空则永不过期" style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      </Modal>

      {/* Edit Fragment Modal */}
      <Modal title="编辑片段" open={editModal} onOk={handleEdit} onCancel={() => setEditModal(false)}>
        <Form form={editForm} layout="vertical">
          <Form.Item name="content" label="内容"><Input.TextArea rows={3} /></Form.Item>
          <Form.Item name="importance_score" label="重要性 (0-1)">
            <InputNumber min={0} max={1} step={0.1} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="ttl" label="TTL (秒)"><InputNumber min={1} style={{ width: '100%' }} /></Form.Item>
        </Form>
      </Modal>

      {/* Prompt Modal */}
      <Modal title="新建抽取模板" open={promptModal} onOk={handleCreatePrompt} onCancel={() => setPromptModal(false)}>
        <Form form={promptForm} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="description" label="描述"><Input /></Form.Item>
          <Form.Item name="template" label="模板" rules={[{ required: true }]}>
            <Input.TextArea rows={5} placeholder="使用 {variable} 作为占位符" />
          </Form.Item>
        </Form>
      </Modal>

      {/* Batch Operation Bar */}
      <BatchOperationBar
        selectedCount={selectedFragmentIds.length}
        onClear={() => setSelectedFragmentIds([])}
        actions={[
          {
            label: '批量归档',
            icon: <InboxOutlined />,
            onClick: handleBatchArchiveFragments,
          } as BatchAction,
          {
            label: '批量删除',
            icon: <DeleteOutlined />,
            onClick: handleBatchDeleteFragments,
            danger: true,
            loading: batchDeleteFragments.isPending,
          } as BatchAction,
        ]}
      />
    </div>
  );
}
