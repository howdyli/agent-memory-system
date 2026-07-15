import { useState, useEffect, useCallback } from 'react';
import {
  Card, Table, Button, Modal, Form, Input, InputNumber, Space,
  Popconfirm, Popover, Tag, Alert, message,
} from 'antd';
import {
  PlusOutlined, DeleteOutlined, EditOutlined, ReloadOutlined,
  ClockCircleOutlined, FieldTimeOutlined,
} from '@ant-design/icons';
import {
  useVariables, useSetVariable, useUpdateVariable,
  useDeleteVariable, useUpdateVariableTtl,
} from '../hooks/useMemoryQueries';

interface Variable {
  key: string;
  value: unknown;
  ttl?: number | null;
  expires_at?: string | null;
}

// ---------- helpers ----------

function formatRemaining(seconds: number): string {
  if (seconds <= 0) return '已过期';
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  const parts: string[] = [];
  if (d > 0) parts.push(`${d}d`);
  if (h > 0) parts.push(`${h}h`);
  if (m > 0) parts.push(`${m}m`);
  parts.push(`${s}s`);
  return parts.join(' ');
}

function getTtlColor(seconds: number | null | undefined): string {
  if (seconds == null) return 'default'; // 永不过期
  if (seconds <= 0) return 'error';
  if (seconds < 600) return 'error';       // < 10min 红
  if (seconds < 3600) return 'warning';    // < 1h 橙
  return 'success';                        // > 1h 绿
}

const TTL_PRESETS = [
  { label: '1 小时', value: 3600 },
  { label: '6 小时', value: 21600 },
  { label: '12 小时', value: 43200 },
  { label: '24 小时', value: 86400 },
  { label: '7 天', value: 604800 },
  { label: '永不过期', value: 0 },
];

// ---------- component ----------

export default function VariablesPage() {
  const { data: rawVariables, isLoading, refetch } = useVariables();
  const setVar = useSetVariable();
  const updateVar = useUpdateVariable();
  const deleteVar = useDeleteVariable();
  const updateTtl = useUpdateVariableTtl();

  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<Variable | null>(null);
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  const [form] = Form.useForm();

  // Tick state for live countdown
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, []);

  // Compute remaining seconds for each variable based on expires_at
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const computeRemaining = useCallback((v: Variable): number | null => {
    if (v.ttl == null) return null; // 永久
    if (v.expires_at) {
      const exp = new Date(v.expires_at).getTime();
      const remaining = Math.max(0, Math.round((exp - Date.now()) / 1000));
      return remaining;
    }
    return null;
  }, []);

  // Normalize data shape – backend returns an array when detailed=true
  const variables: Variable[] = (() => {
    const data = rawVariables;
    if (!data) return [];
    if (Array.isArray(data)) return data as Variable[];
    if (typeof data === 'object') {
      return Object.entries(data as Record<string, unknown>).map(([k, v]) => ({
        key: k, value: v, ttl: null, expires_at: null,
      }));
    }
    return [];
  })();

  // Count expiring-soon variables (< 1h)
  const expiringSoon = variables.filter((v) => {
    const r = computeRemaining(v);
    return r != null && r > 0 && r < 3600;
  });

  // Force re-render every second for countdown (tick is read via computeRemaining's closure)
  // We use a render trigger:
  const [renderKey, setRenderKey] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setRenderKey((k) => k + 1), 1000);
    return () => clearInterval(id);
  }, []);

  // ---------- handlers ----------

  const openCreate = () => { setEditing(null); form.resetFields(); setModalOpen(true); };
  const openEdit = (v: Variable) => {
    setEditing(v);
    form.setFieldsValue({ ...v, ttl: v.ttl ?? undefined });
    setModalOpen(true);
  };

  const handleSave = async () => {
    const vals = await form.validateFields();
    try {
      if (editing) {
        await updateVar.mutateAsync({ key: editing.key, value: vals.value });
        if (vals.ttl !== undefined) {
          await updateTtl.mutateAsync({ key: editing.key, ttl: vals.ttl ?? null });
        }
        message.success('更新成功');
      } else {
        await setVar.mutateAsync({ key: vals.key, value: vals.value, ttl: vals.ttl });
        message.success('创建成功');
      }
      setModalOpen(false);
    } catch {
      message.error('操作失败');
    }
  };

  const handleDelete = async (key: string) => {
    try { await deleteVar.mutateAsync(key); message.success('删除成功'); }
    catch { message.error('删除失败'); }
  };

  const handleBatchDelete = async () => {
    try {
      await Promise.all(selectedKeys.map(k => deleteVar.mutateAsync(k)));
      message.success(`已删除 ${selectedKeys.length} 个变量`);
      setSelectedKeys([]);
    } catch { message.error('批量删除失败'); }
  };

  const handleRenew = async (key: string, ttl: number) => {
    try {
      await updateTtl.mutateAsync({ key, ttl: ttl === 0 ? null : ttl });
      message.success(ttl === 0 ? '已设为永不过期' : '续期成功');
    } catch {
      message.error('续期失败');
    }
  };

  // ---------- render ----------
  // Use renderKey to force re-render for countdown (avoids lint warning on unused tick)
  void renderKey;

  const columns = [
    {
      title: '键名', dataIndex: 'key',
      sorter: (a: Variable, b: Variable) => a.key.localeCompare(b.key),
    },
    {
      title: '值', dataIndex: 'value', ellipsis: true,
      render: (v: unknown) => typeof v === 'object' ? JSON.stringify(v) : String(v ?? ''),
    },
    {
      title: <span><ClockCircleOutlined /> TTL 剩余</span>,
      dataIndex: 'ttl',
      width: 180,
      sorter: (a: Variable, b: Variable) => {
        const ra = computeRemaining(a) ?? Number.MAX_SAFE_INTEGER;
        const rb = computeRemaining(b) ?? Number.MAX_SAFE_INTEGER;
        return ra - rb;
      },
      render: (_: unknown, record: Variable) => {
        const remaining = computeRemaining(record);
        if (remaining == null) {
          return <Tag color="default">永久</Tag>;
        }
        if (remaining <= 0) {
          return <Tag color="error">已过期</Tag>;
        }
        const color = getTtlColor(remaining);
        return (
          <Tag color={color} icon={<FieldTimeOutlined />}>
            {formatRemaining(remaining)}
          </Tag>
        );
      },
    },
    {
      title: '操作', width: 260,
      render: (_: unknown, record: Variable) => (
        <Space>
          <Popover
            title="续期 / 更新 TTL"
            trigger="click"
            content={
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8, minWidth: 200 }}>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                  {TTL_PRESETS.map((p) => (
                    <Button
                      key={p.value}
                      size="small"
                      onClick={() => handleRenew(record.key, p.value)}
                    >
                      {p.label}
                    </Button>
                  ))}
                </div>
                <InputNumber
                  size="small"
                  min={1}
                  placeholder="自定义秒数，按回车确认"
                  style={{ width: '100%' }}
                  onPressEnter={(e) => {
                    const val = Number((e.target as HTMLInputElement).value);
                    if (val > 0) handleRenew(record.key, val);
                  }}
                />
              </div>
            }
          >
            <Button size="small" icon={<ClockCircleOutlined />}>续期</Button>
          </Popover>
          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(record)}>编辑</Button>
          <Popconfirm title="确认删除？" onConfirm={() => handleDelete(record.key)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div className="page-header">
        <h2>记忆变量</h2>
        <p>键值对形式的轻量记忆存储，支持 TTL 过期</p>
      </div>

      {expiringSoon.length > 0 && (
        <Alert
          type="warning"
          showIcon
          icon={<ClockCircleOutlined />}
          message={`有 ${expiringSoon.length} 个变量即将在 1 小时内过期`}
          description={
            <span>
              {expiringSoon.map((v) => (
                <Tag key={v.key} color="warning" style={{ marginRight: 4, marginBottom: 4 }}>
                  {v.key}: {formatRemaining(computeRemaining(v) ?? 0)}
                </Tag>
              ))}
            </span>
          }
          style={{ marginBottom: 16 }}
          closable
        />
      )}

      <Card className="section-card" extra={
        <Space>
          {selectedKeys.length > 0 && (
            <Popconfirm title={`确认删除 ${selectedKeys.length} 个变量？`} onConfirm={handleBatchDelete}>
              <Button danger icon={<DeleteOutlined />}>删除选中 ({selectedKeys.length})</Button>
            </Popconfirm>
          )}
          <Button icon={<ReloadOutlined />} onClick={() => refetch()}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新建变量</Button>
        </Space>
      }>
        <Table
          dataSource={variables}
          rowKey="key"
          loading={isLoading}
          columns={columns}
          rowSelection={{
            selectedRowKeys: selectedKeys,
            onChange: (keys) => setSelectedKeys(keys as string[]),
          }}
          locale={{ emptyText: '暂无记忆变量' }}
        />
      </Card>

      <Modal
        title={editing ? '编辑变量' : '新建变量'}
        open={modalOpen}
        onOk={handleSave}
        onCancel={() => setModalOpen(false)}
        destroyOnClose
      >
        <Form form={form} layout="vertical">
          {!editing && (
            <Form.Item name="key" label="键名" rules={[{ required: true, message: '请输入键名' }]}>
              <Input placeholder="例如: user_preference" />
            </Form.Item>
          )}
          {editing && <Form.Item label="键名"><Input value={editing.key} disabled /></Form.Item>}
          <Form.Item name="value" label="值" rules={[{ required: true, message: '请输入值' }]}>
            <Input.TextArea rows={3} placeholder="支持字符串、数字、JSON" />
          </Form.Item>
          <Form.Item name="ttl" label="过期时间（秒）">
            <InputNumber
              min={0}
              placeholder="默认 86400（24 小时），0 表示永久"
              style={{ width: '100%' }}
            />
          </Form.Item>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: -8, marginBottom: 16 }}>
            {TTL_PRESETS.map((p) => (
              <Tag
                key={p.value}
                style={{ cursor: 'pointer' }}
                onClick={() => form.setFieldValue('ttl', p.value || undefined)}
              >
                {p.label}
              </Tag>
            ))}
          </div>
        </Form>
      </Modal>
    </div>
  );
}
