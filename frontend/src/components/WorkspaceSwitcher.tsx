import { useEffect } from 'react';
import { Dropdown, Button, Spin } from 'antd';
import { TeamOutlined, CheckOutlined } from '@ant-design/icons';
import { useWorkspaceStore } from '../stores/workspaceStore';

export default function WorkspaceSwitcher() {
  const { workspaces, currentWorkspaceId, loading, loadWorkspaces, switchWorkspace } = useWorkspaceStore();

  useEffect(() => {
    loadWorkspaces();
  }, [loadWorkspaces]);

  const current = workspaces.find(w => w.id === currentWorkspaceId);

  const menuItems = workspaces.map(ws => ({
    key: String(ws.id),
    icon: ws.id === currentWorkspaceId ? <CheckOutlined style={{ color: '#52c41a' }} /> : undefined,
    label: (
      <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span>{ws.name}</span>
        <span style={{ fontSize: 11, color: '#999' }}>{ws.kind}</span>
      </span>
    ),
  }));

  if (loading && workspaces.length === 0) {
    return <Spin size="small" />;
  }

  if (workspaces.length === 0) {
    return null; // 无 workspace 时不渲染
  }

  return (
    <Dropdown
      menu={{
        items: menuItems,
        onClick: ({ key }) => switchWorkspace(Number(key)),
      }}
      placement="bottomLeft"
    >
      <Button type="text" size="small" icon={<TeamOutlined />} style={{ gap: 6, fontSize: 13 }}>
        {current?.name || 'Workspace'}
      </Button>
    </Dropdown>
  );
}
