import { useEffect, useState } from 'react';
import {
  Card, Table, Button, Modal, Form, Input, Select, Space, Tag, App, Popconfirm, Tabs,
} from 'antd';
import { PlusOutlined, DeleteOutlined, TeamOutlined, KeyOutlined } from '@ant-design/icons';
import { workspaceApi, apiKeyApi, type Workspace, type ApiKey } from '../services/api';
import { useWorkspaceStore } from '../stores/workspaceStore';

export default function WorkspaceSettings() {
  const { message: msgApi } = App.useApp();
  const { currentWorkspaceId, loadWorkspaces } = useWorkspaceStore();

  // Workspace state
  const [wsList, setWsList] = useState<Workspace[]>([]);
  const [wsLoading, setWsLoading] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [memberOpen, setMemberOpen] = useState(false);
  const [selectedWs, setSelectedWs] = useState<Workspace | null>(null);
  const [createForm] = Form.useForm();
  const [memberForm] = Form.useForm();

  // API Key state
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [keysLoading, setKeysLoading] = useState(false);
  const [keyCreateOpen, setKeyCreateOpen] = useState(false);
  const [keyForm] = Form.useForm();
  const [newKeyValue, setNewKeyValue] = useState<string | null>(null);

  // ---- Workspace ----
  const fetchWorkspaces = async () => {
    setWsLoading(true);
    try {
      const res = await workspaceApi.list();
      setWsList(Array.isArray(res.data) ? res.data : []);
    } catch { /* ignore */ }
    setWsLoading(false);
  };

  useEffect(() => { fetchWorkspaces(); }, []);

  const handleCreateWs = async (values: { name: string; slug: string; kind: string }) => {
    try {
      await workspaceApi.create(values);
      msgApi.success('Workspace 创建成功');
      setCreateOpen(false);
      createForm.resetFields();
      fetchWorkspaces();
      loadWorkspaces();
    } catch { /* ignore */ }
  };

  const handleAddMember = async (values: { user_id: number; role: string }) => {
    if (!selectedWs) return;
    try {
      await workspaceApi.addMember(selectedWs.id, values);
      msgApi.success('成员已添加');
      setMemberOpen(false);
      memberForm.resetFields();
    } catch { /* ignore */ }
  };

  // ---- API Key ----
  const fetchKeys = async () => {
    setKeysLoading(true);
    try {
      const res = await apiKeyApi.list();
      setKeys(Array.isArray(res.data) ? res.data : []);
    } catch { /* ignore */ }
    setKeysLoading(false);
  };

  useEffect(() => { fetchKeys(); }, [currentWorkspaceId]);

  const handleCreateKey = async (values: { name: string; scopes?: string | string[]; expires_at?: string }) => {
    try {
      // scopes 处理：支持字符串（逗号分隔）或数组
      let scopes: string[] | undefined;
      if (values.scopes) {
        scopes = Array.isArray(values.scopes)
          ? values.scopes
          : values.scopes.split(',').map(s => s.trim()).filter(Boolean);
      }
      const payload = {
        name: values.name,
        ...(scopes && { scopes }),
        ...(values.expires_at && { expires_at: values.expires_at }),
      };
      const res = await apiKeyApi.create(payload);
      setNewKeyValue(res.data.key);
      msgApi.success('API Key 已创建，请复制保存（仅显示一次）');
      setKeyCreateOpen(false);
      keyForm.resetFields();
      fetchKeys();
    } catch { /* ignore */ }
  };

  const handleRevokeKey = async (keyId: number) => {
    try {
      await apiKeyApi.revoke(keyId);
      msgApi.success('API Key 已撤销');
      fetchKeys();
    } catch { /* ignore */ }
  };

  // ---- Columns ----
  const wsColumns = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    { title: '名称', dataIndex: 'name' },
    { title: 'Slug', dataIndex: 'slug' },
    { title: '类型', dataIndex: 'kind', render: (v: string) => <Tag color={v === 'personal' ? 'blue' : 'green'}>{v}</Tag> },
    { title: '角色', dataIndex: 'role', render: (v: string) => v ? <Tag>{v}</Tag> : '-' },
  ];

  const keyColumns = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    { title: '名称', dataIndex: 'name' },
    { title: 'Key 前缀', dataIndex: 'key_prefix', render: (v: string) => <code>{v}...</code> },
    { title: '权限', dataIndex: 'scopes', render: (v: string | string[]) => {
          const arr = Array.isArray(v) ? v : (typeof v === 'string' && v ? v.split(',').map(s => s.trim()).filter(Boolean) : []);
          return arr.length ? arr.map(s => <Tag key={s}>{s}</Tag>) : '-';
        }},
    { title: '最后使用', dataIndex: 'last_used_at', render: (v: string) => v || '从未' },
    { title: '过期时间', dataIndex: 'expires_at', render: (v: string) => v || '永不过期' },
    {
      title: '操作', width: 80,
      render: (_: unknown, r: ApiKey) => (
        <Popconfirm title="确定撤销此 API Key？" onConfirm={() => handleRevokeKey(r.id)}>
          <Button type="link" danger size="small" icon={<DeleteOutlined />} />
        </Popconfirm>
      ),
    },
  ];

  const tabItems = [
    {
      key: 'workspaces',
      label: <span><TeamOutlined /> Workspace 管理</span>,
      children: (
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
              创建 Workspace
            </Button>
          </div>
          <Table
            dataSource={wsList}
            columns={wsColumns}
            rowKey="id"
            loading={wsLoading}
            pagination={false}
            size="small"
            onRow={(record) => ({
              onClick: () => { setSelectedWs(record); setMemberOpen(true); },
              style: { cursor: 'pointer' },
            })}
          />
        </Space>
      ),
    },
    {
      key: 'api-keys',
      label: <span><KeyOutlined /> API Key 管理</span>,
      children: (
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          {newKeyValue && (
            <Card size="small" title="新 API Key（请复制保存，仅显示一次）" style={{ borderColor: '#52c41a' }}>
              <Input.TextArea value={newKeyValue} readOnly rows={2} />
              <Button size="small" style={{ marginTop: 8 }} onClick={() => { navigator.clipboard.writeText(newKeyValue); msgApi.success('已复制'); }}>
                复制
              </Button>
              <Button size="small" style={{ marginTop: 8, marginLeft: 8 }} onClick={() => setNewKeyValue(null)}>
                关闭
              </Button>
            </Card>
          )}
          <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setKeyCreateOpen(true)}>
              创建 API Key
            </Button>
          </div>
          <Table dataSource={keys} columns={keyColumns} rowKey="id" loading={keysLoading} pagination={false} size="small" />
        </Space>
      ),
    },
  ];

  return (
    <div style={{ padding: 24 }}>
      <h2>Workspace 设置</h2>
      <Tabs items={tabItems} defaultActiveKey="workspaces" />

      {/* 创建 Workspace 弹窗 */}
      <Modal title="创建 Workspace" open={createOpen} onCancel={() => setCreateOpen(false)} onOk={() => createForm.submit()} destroyOnClose>
        <Form form={createForm} layout="vertical" onFinish={handleCreateWs}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="slug" label="Slug" rules={[{ required: true, pattern: /^[a-z0-9-]+$/, message: '仅允许小写字母、数字、连字符' }]}><Input /></Form.Item>
          <Form.Item name="kind" label="类型" initialValue="team">
            <Select options={[{ value: 'personal', label: '个人' }, { value: 'team', label: '团队' }]} />
          </Form.Item>
        </Form>
      </Modal>

      {/* 成员管理弹窗 */}
      <Modal title={`成员管理 - ${selectedWs?.name || ''}`} open={memberOpen} onCancel={() => setMemberOpen(false)} footer={null} destroyOnClose>
        <Form form={memberForm} layout="inline" onFinish={handleAddMember} style={{ marginBottom: 16 }}>
          <Form.Item name="user_id" rules={[{ required: true, message: '请输入用户 ID' }]}>
            <Input type="number" placeholder="用户 ID" />
          </Form.Item>
          <Form.Item name="role" initialValue="member">
            <Select style={{ width: 120 }} options={[
              { value: 'admin', label: 'Admin' },
              { value: 'member', label: 'Member' },
              { value: 'viewer', label: 'Viewer' },
            ]} />
          </Form.Item>
          <Form.Item><Button type="primary" htmlType="submit" icon={<PlusOutlined />}>添加</Button></Form.Item>
        </Form>
        <p style={{ color: '#999', fontSize: 12 }}>点击 workspace 行可查看，此处仅展示添加/移除操作。</p>
      </Modal>

      {/* 创建 API Key 弹窗 */}
      <Modal title="创建 API Key" open={keyCreateOpen} onCancel={() => { setKeyCreateOpen(false); keyForm.resetFields(); }} onOk={() => keyForm.submit()} destroyOnClose>
        <Form form={keyForm} layout="vertical" onFinish={handleCreateKey}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input placeholder="如：CI/CD 部署 Key" /></Form.Item>
          <Form.Item name="scopes" label="权限范围" help="逗号分隔，如 memory:read,memory:write">
            <Input placeholder="memory:read,memory:write" />
          </Form.Item>
          <Form.Item name="expires_at" label="过期时间" help="ISO 格式，留空表示永不过期">
            <Input placeholder="2025-12-31T23:59:59" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
