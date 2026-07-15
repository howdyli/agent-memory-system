import { useEffect, useState } from 'react';
import {
  Card, Table, Button, Tag, Space, message, Empty, Tabs, Descriptions,
  Input, Popconfirm, Modal, Form, Select, InputNumber, Switch, Tooltip,
} from 'antd';
import {
  ReloadOutlined, AppstoreOutlined, DashboardOutlined, SafetyOutlined,
  PlusOutlined, DeleteOutlined, CheckCircleOutlined, CloseCircleOutlined,
  QuestionCircleOutlined, MedicineBoxOutlined,
} from '@ant-design/icons';
import { systemApi } from '../services/api';
import {
  useLLMBackends, useRegisterLLMBackend, useDeleteLLMBackend,
  useSetDefaultLLMBackend, type LLMBackendRow,
} from '../hooks/useMemoryQueries';

const PROVIDER_OPTIONS = [
  { value: 'openai', label: 'OpenAI' },
  { value: 'anthropic', label: 'Anthropic' },
  { value: 'deepseek', label: 'DeepSeek' },
  { value: 'custom', label: '自定义' },
  { value: 'local', label: '本地模型' },
];

interface HealthState {
  status: string;
  message: string;
  loading?: boolean;
}

export default function SystemPage() {
  const [health, setHealth] = useState<Record<string, unknown>>({});
  const [llmStatus, setLlmStatus] = useState<Record<string, unknown>>({});
  const [plugins, setPlugins] = useState<Record<string, unknown>[]>([]);
  const [perf, setPerf] = useState<Record<string, unknown>>({});
  const [cacheStats, setCacheStats] = useState<Record<string, unknown>>({});
  const [securityResult, setSecurityResult] = useState<any>(null);
  const [securityInput, setSecurityInput] = useState('');

  const { data: backends = [], isLoading: backendsLoading, refetch: refetchBackends } = useLLMBackends();
  const registerBackend = useRegisterLLMBackend();
  const deleteBackend = useDeleteLLMBackend();
  const setDefaultBackend = useSetDefaultLLMBackend();
  const [modalOpen, setModalOpen] = useState(false);
  const [form] = Form.useForm();
  const [healthMap, setHealthMap] = useState<Record<string, HealthState>>({});

  const fetchHealth = async () => {
    try { const res = await systemApi.health(); setHealth(res.data); }
    catch { message.error('健康检查失败'); }
  };

  const fetchLlmStatus = async () => {
    try { const res = await systemApi.llmStatus(); setLlmStatus(res.data); }
    catch { message.error('获取状态失败'); }
  };

  const switchLLM = async (backend: string) => {
    try { await systemApi.switchLLM(backend); message.success(`已切换到 ${backend}`); fetchLlmStatus(); }
    catch { message.error('切换失败'); }
  };

  const fetchPlugins = async () => {
    try { const res = await systemApi.plugins(); setPlugins(res.data?.plugins || res.data || []); }
    catch { message.error('获取插件失败'); }
  };

  const fetchPerf = async () => {
    try { const res = await systemApi.performance(); setPerf(res.data); }
    catch { message.error('获取性能数据失败'); }
  };

  const fetchCache = async () => {
    try { const res = await systemApi.cacheStats(); setCacheStats(res.data); }
    catch { message.error('获取缓存统计失败'); }
  };

  const clearCache = async () => {
    try { await systemApi.clearCache(); message.success('缓存已清除'); fetchCache(); }
    catch { message.error('清除失败'); }
  };

  const handleSecurityCheck = async () => {
    if (!securityInput.trim()) return;
    try { const res = await systemApi.securityCheck(securityInput); setSecurityResult(res.data); }
    catch { message.error('检查失败'); }
  };

  const handleAddBackend = async () => {
    const vals = await form.validateFields();
    try {
      await registerBackend.mutateAsync({
        backend_name: vals.name,
        backend_type: vals.provider,
        config: {
          api_key: vals.api_key,
          model: vals.model,
          base_url: vals.base_url,
          timeout: vals.timeout,
        },
        set_active: vals.set_active,
      });
      message.success('后端注册成功');
      setModalOpen(false);
      form.resetFields();
    } catch {
      message.error('注册失败');
    }
  };

  const handleDeleteBackend = async (name: string) => {
    try { await deleteBackend.mutateAsync(name); message.success('已删除'); }
    catch { message.error('删除失败'); }
  };

  const handleSetDefault = async (name: string) => {
    try { await setDefaultBackend.mutateAsync(name); message.success(`已设置 ${name} 为默认后端`); }
    catch { message.error('设置默认后端失败'); }
  };

  const handleHealthCheck = async (name: string) => {
    setHealthMap(prev => ({ ...prev, [name]: { status: 'unknown', message: '', loading: true } }));
    try {
      const res = await systemApi.checkLLMBackendHealth(name);
      const data = res.data || {};
      setHealthMap(prev => ({
        ...prev,
        [name]: { status: data.status || 'unknown', message: data.message || '', loading: false },
      }));
      if (data.status === 'healthy') {
        message.success(`${name} 健康检查通过`);
      } else {
        message.warning(`${name} 状态异常：${data.message || ''}`);
      }
    } catch {
      setHealthMap(prev => ({ ...prev, [name]: { status: 'unhealthy', message: '请求失败', loading: false } }));
      message.error('健康检查请求失败');
    }
  };

  const renderHealthStatus = (name: string) => {
    const h = healthMap[name];
    if (!h || (!h.status && !h.loading)) {
      return <Tag icon={<QuestionCircleOutlined />} color="default">未检查</Tag>;
    }
    if (h.loading) return <Tag color="processing">检查中...</Tag>;
    const color = h.status === 'healthy' ? 'success' : h.status === 'degraded' ? 'warning' : 'error';
    const icon = h.status === 'healthy' ? <CheckCircleOutlined /> : <CloseCircleOutlined />;
    const label = h.status === 'healthy' ? '健康' : h.status === 'degraded' ? '降级' : h.status === 'unknown' ? '未知' : '异常';
    return (
      <Tooltip title={h.message}>
        <Tag icon={icon} color={color}>{label}</Tag>
      </Tooltip>
    );
  };

  useEffect(() => { fetchHealth(); }, []);

  const llmConfigTab = (
    <>
      <Card
        title="LLM 后端配置"
        className="section-card"
        extra={
          <Space>
            <Button icon={<ReloadOutlined />} onClick={() => refetchBackends()}>刷新</Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={() => { form.resetFields(); setModalOpen(true); }}>新增后端</Button>
          </Space>
        }
      >
        {backends.length === 0 ? <Empty description="暂无已注册后端，点击新增" /> : (
          <Table
            dataSource={backends as LLMBackendRow[]}
            rowKey="name"
            size="small"
            pagination={false}
            loading={backendsLoading}
            columns={[
              { title: '名称', dataIndex: 'name', render: (t: string) => <Tag color="blue">{t}</Tag> },
              { title: '提供商', dataIndex: 'type', render: (t: string) => <Tag>{t}</Tag> },
              { title: '模型ID', dataIndex: 'model', render: (t?: string) => t || '-' },
              { title: 'Base URL', dataIndex: 'base_url', ellipsis: true, render: (t?: string) => t || '-' },
              { title: 'API Key', dataIndex: 'api_key_masked', render: (t?: string) => t || '-' },
              { title: '默认', dataIndex: 'is_default', render: (v: boolean) => v ? <Tag color="green">默认</Tag> : '-' },
              { title: '健康状态', render: (_, r) => renderHealthStatus(r.name) },
              {
                title: '操作',
                width: 240,
                render: (_, r) => (
                  <Space>
                    {!r.is_default && (
                      <Button size="small" type="primary" onClick={() => handleSetDefault(r.name)}>设为默认</Button>
                    )}
                    <Button size="small" icon={<MedicineBoxOutlined />} onClick={() => handleHealthCheck(r.name)}>健康检查</Button>
                    <Popconfirm title={`确认删除后端 ${r.name}？`} onConfirm={() => handleDeleteBackend(r.name)}>
                      <Button size="small" danger icon={<DeleteOutlined />} />
                    </Popconfirm>
                  </Space>
                ),
              },
            ]}
          />
        )}
      </Card>
      <Modal
        title="新增 LLM 后端"
        open={modalOpen}
        onOk={handleAddBackend}
        onCancel={() => setModalOpen(false)}
        destroyOnClose
        width={560}
      >
        <Form form={form} layout="vertical" initialValues={{ provider: 'openai', timeout: 30, set_active: false }}>
          <Form.Item name="name" label="后端名称" rules={[{ required: true, message: '请输入后端名称' }]}>
            <Input placeholder="例如：my-deepseek" />
          </Form.Item>
          <Form.Item name="provider" label="提供商" rules={[{ required: true }]}>
            <Select options={PROVIDER_OPTIONS} />
          </Form.Item>
          <Form.Item name="api_key" label="API Key" rules={[{ required: true, message: '请输入 API Key' }]}>
            <Input.Password placeholder="sk-..." />
          </Form.Item>
          <Form.Item name="model" label="模型ID" rules={[{ required: true, message: '请输入模型ID' }]}>
            <Input placeholder="例如：deepseek-v4-flash" />
          </Form.Item>
          <Form.Item name="base_url" label="Base URL（可选）">
            <Input placeholder="例如：https://api.deepseek.com/v1" />
          </Form.Item>
          <Form.Item name="timeout" label="超时时间（秒）">
            <InputNumber min={1} max={300} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="set_active" label="设为默认后端" valuePropName="checked">
            <Switch checkedChildren="是" unCheckedChildren="否" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );

  return (
    <div>
      <div className="page-header">
        <h2><DashboardOutlined /> 系统集成</h2>
        <p>LLM 后端管理、插件架构、性能监控、安全审查 {health.status ? <Tag color={health.status === 'healthy' ? 'green' : 'red'} style={{ marginLeft: 8 }}>{String(health.status)}</Tag> : null}</p>
      </div>

      <Tabs items={[
        {
          key: 'llm', label: 'LLM 运行状态',
          children: (
            <>
              <Card title="运行状态" className="section-card" extra={<Button icon={<ReloadOutlined />} onClick={() => { fetchLlmStatus(); refetchBackends(); }}>刷新</Button>}>
                {Object.keys(llmStatus).length > 0 ? (
                  <Descriptions column={2} bordered size="small">
                    {Object.entries(llmStatus).map(([k, v]) => (
                      <Descriptions.Item key={k} label={k}>{String(v)}</Descriptions.Item>
                    ))}
                  </Descriptions>
                ) : <Empty description="点击刷新获取状态" />}
              </Card>
              <Card title="可用后端" className="section-card">
                {backends.length === 0 ? <Empty description="暂无可用后端，点击刷新" /> : (
                  <Table
                    dataSource={backends as LLMBackendRow[]}
                    rowKey="name"
                    size="small"
                    pagination={false}
                    loading={backendsLoading}
                    columns={[
                      { title: '名称', dataIndex: 'name', render: (t: string) => <Tag color="blue">{t}</Tag> },
                      { title: '状态', dataIndex: 'is_active', render: (s: boolean) => <Tag color={s ? 'green' : 'default'}>{s ? 'active' : 'inactive'}</Tag> },
                      { title: '类型', dataIndex: 'type' },
                      { title: '操作', render: (_, r) => (
                        <Button size="small" type={r.is_active ? 'default' : 'primary'} onClick={() => switchLLM(r.name)}>
                          {r.is_active ? '当前' : '切换'}
                        </Button>
                      )},
                    ]}
                  />
                )}
              </Card>
            </>
          ),
        },
        {
          key: 'llm-config', label: 'LLM 后端配置',
          children: llmConfigTab,
        },
        {
          key: 'plugins', label: '插件管理',
          children: (
            <Card className="section-card" extra={<Button icon={<ReloadOutlined />} onClick={fetchPlugins}>刷新</Button>}>
              <Table
                dataSource={plugins}
                rowKey="id"
                size="small"
                pagination={false}
                columns={[
                  { title: '名称', dataIndex: 'name', render: (t: string) => <Tag color="purple"><AppstoreOutlined /> {t}</Tag> },
                  { title: '类型', dataIndex: 'type' },
                  { title: '状态', dataIndex: 'status', render: (s: string) => <Tag color={s === 'active' ? 'green' : 'orange'}>{s}</Tag> },
                  { title: '版本', dataIndex: 'version', width: 80 },
                ]}
                locale={{ emptyText: '暂无已注册插件，点击刷新' }}
              />
            </Card>
          ),
        },
        {
          key: 'performance', label: '性能监控',
          children: (
            <>
              <Card title="系统性能" className="section-card" extra={<Button icon={<ReloadOutlined />} onClick={() => { fetchPerf(); fetchCache(); }}>刷新</Button>}>
                {Object.keys(perf).length > 0 ? (
                  <Descriptions column={2} bordered size="small">
                    {Object.entries(perf).map(([k, v]) => (
                      <Descriptions.Item key={k} label={k}>{String(v)}</Descriptions.Item>
                    ))}
                  </Descriptions>
                ) : <Empty description="点击刷新获取性能数据" />}
              </Card>
              <Card title="缓存统计" className="section-card" extra={
                <Space>
                  <Popconfirm title="确认清除所有缓存？" onConfirm={clearCache}><Button danger size="small">清除缓存</Button></Popconfirm>
                  <Button size="small" onClick={fetchCache}>刷新</Button>
                </Space>
              }>
                {Object.keys(cacheStats).length > 0 ? (
                  <Descriptions column={2} bordered size="small">
                    {Object.entries(cacheStats).map(([k, v]) => (
                      <Descriptions.Item key={k} label={k}>{String(v)}</Descriptions.Item>
                    ))}
                  </Descriptions>
                ) : <Empty description="点击刷新获取缓存统计" />}
              </Card>
            </>
          ),
        },
        {
          key: 'security', label: '安全检查',
          children: (
            <Card className="section-card">
              <Space.Compact style={{ width: '100%', marginBottom: 16 }}>
                <Input value={securityInput} onChange={e => setSecurityInput(e.target.value)} placeholder="输入要检查的内容..." onPressEnter={handleSecurityCheck} />
                <Button type="primary" icon={<SafetyOutlined />} onClick={handleSecurityCheck}>检查</Button>
              </Space.Compact>
              {securityResult !== null && <pre className="code-block">{JSON.stringify(securityResult, null, 2)}</pre>}
            </Card>
          ),
        },
      ]} />
    </div>
  );
}
