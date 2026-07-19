import { useState, useMemo } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { Layout, Menu, Button, Dropdown, Avatar, theme } from 'antd';
import {
  DashboardOutlined,
  KeyOutlined,
  DatabaseOutlined,
  FileTextOutlined,
  SearchOutlined,
  HistoryOutlined,
  SettingOutlined,
  LogoutOutlined,
  UserOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  ThunderboltOutlined,
  RobotOutlined,
  ToolOutlined,
  BarChartOutlined,
  ExperimentOutlined,
  ShareAltOutlined,
  TeamOutlined,
  SwapOutlined,
  MessageOutlined,
  BulbOutlined,
  MonitorOutlined,
} from '@ant-design/icons';
import { useAuthStore } from '../stores/authStore';
import WorkspaceSwitcher from './WorkspaceSwitcher';

const { Header, Sider, Content } = Layout;

// 路由 → 所属分组 key 的映射
const ROUTE_TO_GROUP: Record<string, string> = {
  'agent-chat': 'grp-agent',
  'agent-tools': 'grp-agent',
  'variables': 'grp-memory',
  'extraction': 'grp-memory',
  'tables': 'grp-memory',
  'fragments': 'grp-memory',
  'long-term': 'grp-memory',
  'recall': 'grp-engine',
  'graph-memory': 'grp-engine',
  'hybrid-search': 'grp-engine',
  'observability': 'grp-ops',
  'lifecycle': 'grp-ops',
  'system': 'grp-ops',
  'workspace': 'grp-ops',
};

const menuItems = [
  { key: '/', icon: <DashboardOutlined />, label: '仪表盘' },
  {
    key: 'grp-agent',
    icon: <RobotOutlined />,
    label: 'Agent 交互',
    children: [
      { key: '/agent-chat', icon: <MessageOutlined />, label: 'Agent 对话' },
      { key: '/agent-tools', icon: <ToolOutlined />, label: 'Agent 工具' },
    ],
  },
  {
    key: 'grp-memory',
    icon: <DatabaseOutlined />,
    label: '记忆管理',
    children: [
      { key: '/variables', icon: <KeyOutlined />, label: '记忆变量' },
      { key: '/fragments', icon: <FileTextOutlined />, label: '记忆片段' },
      { key: '/tables', icon: <DatabaseOutlined />, label: '记忆表' },
      { key: '/extraction', icon: <ThunderboltOutlined />, label: '记忆抽取' },
      { key: '/long-term', icon: <HistoryOutlined />, label: '长期记忆' },
    ],
  },
  {
    key: 'grp-engine',
    icon: <BulbOutlined />,
    label: '知识引擎',
    children: [
      { key: '/recall', icon: <SearchOutlined />, label: '自动召回' },
      { key: '/graph-memory', icon: <ShareAltOutlined />, label: '知识图谱' },
      { key: '/hybrid-search', icon: <SwapOutlined />, label: '混合搜索' },
    ],
  },
  {
    key: 'grp-ops',
    icon: <MonitorOutlined />,
    label: '运维监控',
    children: [
      { key: '/observability', icon: <BarChartOutlined />, label: '观测中心' },
      { key: '/lifecycle', icon: <ExperimentOutlined />, label: '生命周期' },
      { key: '/system', icon: <SettingOutlined />, label: '系统集成' },
      { key: '/workspace/settings', icon: <TeamOutlined />, label: 'Workspace' },
    ],
  },
];

export default function AppLayout() {
  const [collapsed, setCollapsed] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const { user, logout } = useAuthStore();
  const { token: themeToken } = theme.useToken();

  const selectedKey = '/' + (location.pathname.split('/')[1] || '');

  // 根据当前路由自动展开对应的子菜单
  const openKeys = useMemo(() => {
    const seg = location.pathname.split('/')[1] || '';
    const group = ROUTE_TO_GROUP[seg];
    return group ? [group] : [];
  }, [location.pathname]);

  const handleLogout = () => {
    logout();
    navigate('/login');
  };

  const userMenu = {
    items: [
      { key: 'user', icon: <UserOutlined />, label: user?.username || 'User', disabled: true },
      { type: 'divider' as const },
      { key: 'logout', icon: <LogoutOutlined />, label: '退出登录', danger: true },
    ],
    onClick: ({ key }: { key: string }) => {
      if (key === 'logout') handleLogout();
    },
  };

  return (
    <Layout className="app-layout">
      <Sider trigger={null} collapsible collapsed={collapsed} className="app-sider" width={220}>
        <div className="app-logo">{collapsed ? 'AM' : 'Agent Memory'}</div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[selectedKey]}
          defaultOpenKeys={openKeys}
          items={menuItems}
          onClick={({ key }) => navigate(key)}
        />
      </Sider>
      <Layout>
        <Header className="app-header" style={{ padding: '0 24px' }}>
          <Button
            type="text"
            icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
            onClick={() => setCollapsed(!collapsed)}
          />
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <WorkspaceSwitcher />
            <Dropdown menu={userMenu} placement="bottomRight">
              <div style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8 }}>
                <Avatar size="small" icon={<UserOutlined />} style={{ backgroundColor: themeToken.colorPrimary }} />
                <span style={{ fontSize: 14, fontWeight: 500 }}>{user?.username}</span>
              </div>
            </Dropdown>
          </div>
        </Header>
        <Content className="app-content">
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
