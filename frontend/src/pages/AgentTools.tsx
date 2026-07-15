import { useEffect, useState, useCallback } from 'react';
import {
  Card,
  Table,
  Tag,
  Button,
  Descriptions,
  Collapse,
  message,
  Drawer,
  Form,
  Space,
  Alert,
  Typography,
  Spin,
  Divider,
} from 'antd';
import {
  ToolOutlined,
  ReloadOutlined,
  ApiOutlined,
  PlayCircleOutlined,
  CopyOutlined,
  ClearOutlined,
} from '@ant-design/icons';
import { agentApi } from '../services/api';
import SchemaForm from '../components/SchemaForm';

const { Text } = Typography;

interface ToolInfo {
  name: string;
  description: string;
  parameters: {
    type: string;
    properties: Record<string, { type: string; description: string; enum?: string[]; items?: unknown; required?: string[] }>;
    required: string[];
  };
}

export default function AgentToolsPage() {
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [rawSchema, setRawSchema] = useState<Record<string, unknown> | null>(null);

  // Drawer state
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [activeTool, setActiveTool] = useState<ToolInfo | null>(null);
  const [executing, setExecuting] = useState(false);
  const [execResult, setExecResult] = useState<unknown>(null);
  const [execError, setExecError] = useState<string | null>(null);
  const [form] = Form.useForm();

  const fetchTools = async () => {
    setLoading(true);
    try {
      const [schemaRes, toolsRes] = await Promise.all([
        agentApi.toolsSchema(),
        agentApi.tools(),
      ]);
      const toolList = toolsRes.data?.tools || [];
      setTools(toolList);
      setRawSchema(schemaRes.data);
    } catch {
      message.error('获取工具列表失败');
    }
    setLoading(false);
  };

  useEffect(() => { fetchTools(); }, []);

  const openTestDrawer = useCallback((tool: ToolInfo) => {
    setActiveTool(tool);
    setExecResult(null);
    setExecError(null);
    form.resetFields();
    setDrawerOpen(true);
  }, [form]);

  const closeDrawer = useCallback(() => {
    setDrawerOpen(false);
    setActiveTool(null);
    setExecResult(null);
    setExecError(null);
  }, []);

  const handleExecute = useCallback(async () => {
    if (!activeTool) return;
    try {
      const values = await form.validateFields();
      setExecuting(true);
      setExecResult(null);
      setExecError(null);
      const res = await agentApi.executeTool(activeTool.name, values);
      setExecResult(res.data);
      message.success(`工具 ${activeTool.name} 执行成功`);
    } catch (err: unknown) {
      if (err && typeof err === 'object' && 'errorFields' in err) {
        // form validation error
        return;
      }
      const axiosErr = err as { response?: { data?: { detail?: string } }; message?: string };
      const detail = axiosErr?.response?.data?.detail || axiosErr?.message || '执行失败';
      setExecError(typeof detail === 'string' ? detail : JSON.stringify(detail));
      message.error('工具执行失败');
    } finally {
      setExecuting(false);
    }
  }, [activeTool, form]);

  const handleReset = useCallback(() => {
    form.resetFields();
    setExecResult(null);
    setExecError(null);
  }, [form]);

  const handleCopy = useCallback(async () => {
    if (!execResult) return;
    try {
      await navigator.clipboard.writeText(JSON.stringify(execResult, null, 2));
      message.success('已复制到剪贴板');
    } catch {
      message.error('复制失败');
    }
  }, [execResult]);

  const columns = [
    {
      title: '工具名称',
      dataIndex: 'name',
      width: 200,
      render: (name: string) => (
        <Tag icon={<ToolOutlined />} color="purple" style={{ fontSize: 13 }}>
          {name}
        </Tag>
      ),
    },
    {
      title: '描述',
      dataIndex: 'description',
      ellipsis: true,
    },
    {
      title: '参数',
      key: 'params',
      render: (_: unknown, record: ToolInfo) => {
        const props = record.parameters?.properties || {};
        const required = record.parameters?.required || [];
        const entries = Object.entries(props);
        if (entries.length === 0) return <Tag>无参数</Tag>;
        return (
          <div>
            {entries.map(([key, val]) => (
              <div key={key} style={{ marginBottom: 4, fontSize: 13 }}>
                <Tag color={required.includes(key) ? 'red' : 'default'} style={{ margin: 0, fontSize: 11 }}>
                  {required.includes(key) ? '必填' : '可选'}
                </Tag>
                <code style={{ marginLeft: 6, fontWeight: 500 }}>{key}</code>
                <span style={{ color: '#888', marginLeft: 4 }}>({val.type})</span>
                {val.enum && (
                  <span style={{ color: '#999', marginLeft: 4 }}>
                    [{val.enum.join(', ')}]
                  </span>
                )}
                <span style={{ color: '#666', marginLeft: 4 }}>— {val.description}</span>
              </div>
            ))}
          </div>
        );
      },
    },
    {
      title: '操作',
      key: 'action',
      width: 100,
      render: (_: unknown, record: ToolInfo) => (
        <Button
          type="primary"
          size="small"
          icon={<PlayCircleOutlined />}
          onClick={() => openTestDrawer(record)}
        >
          测试
        </Button>
      ),
    },
  ];

  return (
    <div>
      <div className="page-header">
        <h2>Agent 工具测试台</h2>
        <p>记忆系统提供的 Agent 可调用工具，支持在线测试执行</p>
      </div>

      <Card
        className="section-card"
        extra={
          <Button icon={<ReloadOutlined />} onClick={fetchTools} loading={loading}>
            刷新
          </Button>
        }
      >
        <Descriptions column={3} style={{ marginBottom: 24 }}>
          <Descriptions.Item label="工具总数">
            <Tag color="blue">{tools.length}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="协议格式">
            <Tag>OpenAI Function Calling</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="状态">
            <Tag color="green">可用</Tag>
          </Descriptions.Item>
        </Descriptions>

        <Table
          dataSource={tools}
          columns={columns}
          rowKey="name"
          loading={loading}
          size="middle"
          expandable={{
            expandedRowRender: (record: ToolInfo) => (
              <pre
                style={{
                  background: '#f5f5f5',
                  padding: 12,
                  borderRadius: 6,
                  fontSize: 12,
                  maxHeight: 300,
                  overflow: 'auto',
                  margin: 0,
                }}
              >
                {JSON.stringify(record.parameters, null, 2)}
              </pre>
            ),
          }}
          pagination={{ pageSize: 7, showSizeChanger: false, showTotal: (total) => `共 ${total} 个工具` }}
          locale={{ emptyText: '暂无可用工具' }}
        />
      </Card>

      {rawSchema && (
        <Card title="原始 Schema（OpenAI 格式）" style={{ marginTop: 16 }} className="section-card">
          <Collapse
            items={[
              {
                key: 'schema',
                label: (
                  <span>
                    <ApiOutlined /> 查看完整 Tool Schema JSON
                  </span>
                ),
                children: (
                  <pre
                    style={{
                      background: '#f5f5f5',
                      padding: 16,
                      borderRadius: 6,
                      fontSize: 12,
                      overflow: 'auto',
                      maxHeight: 400,
                    }}
                  >
                    {JSON.stringify(rawSchema as Record<string, unknown>, null, 2)}
                  </pre>
                ),
              },
            ]}
          />
        </Card>
      )}

      {/* 工具测试执行 Drawer */}
      <Drawer
        title={
          <Space>
            <PlayCircleOutlined />
            <span>测试工具：{activeTool?.name}</span>
          </Space>
        }
        placement="right"
        width={640}
        open={drawerOpen}
        onClose={closeDrawer}
        destroyOnClose
        footer={
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
            <Button icon={<ClearOutlined />} onClick={handleReset}>
              重置表单
            </Button>
            <Button icon={<CopyOutlined />} onClick={handleCopy} disabled={!execResult}>
              复制结果
            </Button>
            <Button
              type="primary"
              icon={<PlayCircleOutlined />}
              loading={executing}
              onClick={handleExecute}
            >
              执行
            </Button>
          </div>
        }
      >
        {activeTool && (
          <>
            <Alert
              message={activeTool.description}
              type="info"
              showIcon
              style={{ marginBottom: 16 }}
            />

            <Divider orientation="left" style={{ fontSize: 13 }}>
              参数
            </Divider>

            <Form form={form} layout="vertical">
              <SchemaForm schema={activeTool.parameters} form={form} />
            </Form>

            <Divider orientation="left" style={{ fontSize: 13 }}>
              执行结果
            </Divider>

            {executing && (
              <div style={{ textAlign: 'center', padding: 24 }}>
                <Spin tip="执行中..." />
              </div>
            )}

            {execError && (
              <Alert
                message="执行失败"
                description={execError}
                type="error"
                showIcon
                closable
                style={{ marginBottom: 12 }}
              />
            )}

            {execResult && !executing && (
              <pre
                style={{
                  background: '#f6f8fa',
                  border: '1px solid #e1e4e8',
                  padding: 16,
                  borderRadius: 6,
                  fontSize: 12,
                  maxHeight: 400,
                  overflow: 'auto',
                  margin: 0,
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-all',
                }}
              >
                {JSON.stringify(execResult, null, 2)}
              </pre>
            )}

            {!execResult && !execError && !executing && (
              <Text type="secondary">点击「执行」按钮查看结果</Text>
            )}
          </>
        )}
      </Drawer>
    </div>
  );
}
