import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Button,
  Typography,
  Popconfirm,
  Input,
  Dropdown,
  Empty,
  Tooltip,
  Checkbox,
  Modal,
  Spin,
  Badge,
  Space,
} from 'antd';
import {
  PlusOutlined,
  DeleteOutlined,
  EditOutlined,
  MessageOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  SearchOutlined,
} from '@ant-design/icons';
import type { MenuProps } from 'antd';
import {
  useInfiniteSessionList,
  useSearchSessions,
  useBatchDeleteSessions,
} from '../hooks/useMemoryQueries';
import type { Session } from '../services/api';

const { Text } = Typography;
const { Search } = Input;

interface SessionSidebarProps {
  activeSessionId?: string;
  onSelect: (sessionId: string) => void;
  onDelete: (sessionId: string) => void;
  onRename: (sessionId: string, title: string) => void;
  onCreate: () => void;
  onBatchDelete?: (deletedIds: string[]) => void;
}

function formatTime(iso?: string) {
  if (!iso) return '';
  const d = new Date(iso);
  const now = new Date();
  const isToday = d.toDateString() === now.toDateString();
  const isYesterday = d.toDateString() === new Date(now.getTime() - 86400000).toDateString();
  if (isToday) return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
  if (isYesterday) return '昨天';
  return d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
}

function groupByTime(sessions: Session[]) {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today.getTime() - 86400000);
  const weekAgo = new Date(today.getTime() - 7 * 86400000);

  const groups: { label: string; items: Session[] }[] = [
    { label: '今天', items: [] },
    { label: '昨天', items: [] },
    { label: '本周', items: [] },
    { label: '更早', items: [] },
  ];

  for (const s of sessions) {
    const d = new Date(s.updated_at);
    if (d >= today) groups[0].items.push(s);
    else if (d >= yesterday) groups[1].items.push(s);
    else if (d >= weekAgo) groups[2].items.push(s);
    else groups[3].items.push(s);
  }

  return groups.filter(g => g.items.length > 0);
}

function HighlightText({ text, keyword }: { text: string; keyword: string }) {
  if (!keyword.trim()) return <>{text}</>;
  const parts = text.split(new RegExp(`(${keyword.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi'));
  return (
    <>
      {parts.map((part, i) =>
        part.toLowerCase() === keyword.toLowerCase() ? (
          <span key={i} style={{ background: '#ffe58f', borderRadius: 2, padding: '0 2px' }}>
            {part}
          </span>
        ) : (
          <span key={i}>{part}</span>
        )
      )}
    </>
  );
}

const SIDEBAR_WIDTH = 280;
const SIDEBAR_COLLAPSED_WIDTH = 52;

export default function SessionSidebar({
  activeSessionId,
  onSelect,
  onDelete,
  onRename,
  onCreate,
  onBatchDelete,
}: SessionSidebarProps) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState('');
  const [collapsed, setCollapsed] = useState(false);

  const [searchInput, setSearchInput] = useState('');
  const [searchKeyword, setSearchKeyword] = useState('');
  const searchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const [multiSelectMode, setMultiSelectMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  const listQuery = useInfiniteSessionList();
  const searchQuery = useSearchSessions(searchKeyword);
  const batchDeleteMut = useBatchDeleteSessions();

  const isSearchMode = searchKeyword.trim().length > 0;
  const currentQuery = isSearchMode ? searchQuery : listQuery;
  const sessions = useMemo<Session[]>(() => {
    return currentQuery.data?.pages.flat() || [];
  }, [currentQuery.data]);

  const hasNextPage = !!currentQuery.hasNextPage;
  const isFetchingNextPage = !!currentQuery.isFetchingNextPage;
  const isLoading = currentQuery.isLoading;

  // debounce 300ms
  const handleSearchChange = (value: string) => {
    setSearchInput(value);
    if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
    searchTimerRef.current = setTimeout(() => {
      setSearchKeyword(value);
      setSelectedIds(new Set());
    }, 300);
  };

  useEffect(() => {
    return () => {
      if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
    };
  }, []);

  const handleRenameStart = (session: Session) => {
    setEditingId(session.session_id);
    setEditingTitle(session.title);
  };

  const handleRenameConfirm = (sessionId: string) => {
    if (editingTitle.trim()) {
      onRename(sessionId, editingTitle.trim());
    }
    setEditingId(null);
  };

  const toggleSelection = (sid: string) => {
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(sid)) next.delete(sid);
      else next.add(sid);
      return next;
    });
  };

  const toggleMultiSelect = () => {
    setMultiSelectMode(prev => {
      if (prev) setSelectedIds(new Set());
      return !prev;
    });
  };

  const handleBatchDelete = () => {
    if (selectedIds.size === 0) return;
    const ids = Array.from(selectedIds);
    Modal.confirm({
      title: '确认批量删除对话？',
      content: `已选中 ${ids.length} 个对话，删除后不可恢复。`,
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        try {
          await batchDeleteMut.mutateAsync(ids);
          setSelectedIds(new Set());
          setMultiSelectMode(false);
          onBatchDelete?.(ids);
        } catch {
          // error handled by global handler
        }
      },
    });
  };

  const getMenuItems = (session: Session): MenuProps['items'] => [
    {
      key: 'rename',
      icon: <EditOutlined />,
      label: '重命名',
      onClick: () => handleRenameStart(session),
    },
    {
      key: 'multi',
      icon: <span>☑️</span>,
      label: '多选模式',
      onClick: () => {
        setMultiSelectMode(true);
        setSelectedIds(prev => {
          const next = new Set(prev);
          next.add(session.session_id);
          return next;
        });
      },
    },
    { type: 'divider' },
    {
      key: 'delete',
      icon: <DeleteOutlined />,
      label: '删除',
      danger: true,
    },
  ];

  const groups = useMemo(() => groupByTime(sessions), [sessions]);

  // Collapsed mode — show only icons
  if (collapsed) {
    return (
      <div
        style={{
          width: SIDEBAR_COLLAPSED_WIDTH,
          height: '100%',
          borderRight: '1px solid #f0f0f0',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          background: '#fafafa',
          flexShrink: 0,
        }}
      >
        <Tooltip title="展开会话列表" placement="right">
          <Button
            type="text"
            icon={<MenuUnfoldOutlined />}
            onClick={() => setCollapsed(false)}
            style={{ marginTop: 12, fontSize: 16 }}
          />
        </Tooltip>
        <Tooltip title="新建对话" placement="right">
          <Button
            type="text"
            icon={<PlusOutlined />}
            onClick={onCreate}
            style={{ marginTop: 8, fontSize: 16, color: '#1677ff' }}
          />
        </Tooltip>
        <div style={{ flex: 1 }} />
        <div style={{ fontSize: 11, color: '#999', marginBottom: 8, textAlign: 'center' }}>
          {sessions.length}
        </div>
      </div>
    );
  }

  const renderSessionItem = (session: Session) => {
    const isActive = activeSessionId === session.session_id;
    const isSelected = selectedIds.has(session.session_id);

    return (
      <div
        key={session.session_id}
        onClick={() => {
          if (multiSelectMode) {
            toggleSelection(session.session_id);
          } else {
            onSelect(session.session_id);
          }
        }}
        style={{
          padding: '8px 12px',
          borderRadius: 8,
          cursor: 'pointer',
          marginBottom: 2,
          background: isActive ? '#e6f4ff' : isSelected ? '#f6ffed' : 'transparent',
          transition: 'background 0.2s',
        }}
        onMouseEnter={e => {
          if (!isActive && !isSelected) {
            (e.currentTarget as HTMLElement).style.background = '#f0f0f0';
          }
        }}
        onMouseLeave={e => {
          if (!isActive && !isSelected) {
            (e.currentTarget as HTMLElement).style.background = 'transparent';
          }
        }}
      >
        {editingId === session.session_id ? (
          <Input
            size="small"
            value={editingTitle}
            onChange={e => setEditingTitle(e.target.value)}
            onPressEnter={() => handleRenameConfirm(session.session_id)}
            onBlur={() => handleRenameConfirm(session.session_id)}
            autoFocus
            onClick={e => e.stopPropagation()}
          />
        ) : (
          <Dropdown
            menu={{
              items: getMenuItems(session),
              onClick: ({ key, domEvent }) => {
                domEvent.stopPropagation();
                if (key === 'delete') {
                  // handled by Popconfirm below
                }
              },
            }}
            trigger={['contextMenu']}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              {multiSelectMode && (
                <Checkbox
                  checked={isSelected}
                  onClick={e => e.stopPropagation()}
                  onChange={e => {
                    e.stopPropagation();
                    toggleSelection(session.session_id);
                  }}
                />
              )}
              <MessageOutlined style={{ color: '#999', fontSize: 12, flexShrink: 0 }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div
                  style={{
                    fontSize: 13,
                    fontWeight: isActive ? 600 : 400,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {isSearchMode ? (
                    <HighlightText text={session.title} keyword={searchKeyword} />
                  ) : (
                    session.title
                  )}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11 }}>
                  <Text type="secondary">{session.message_count || 0} 条消息</Text>
                  <Text type="secondary">{formatTime(session.updated_at)}</Text>
                </div>
                {isSearchMode && session.highlights && session.highlights.length > 0 && (
                  <div style={{ marginTop: 4 }}>
                    {session.highlights.map((h, i) => (
                      <div
                        key={i}
                        style={{
                          fontSize: 11,
                          color: '#666',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                        }}
                      >
                        <HighlightText text={h} keyword={searchKeyword} />
                      </div>
                    ))}
                  </div>
                )}
              </div>
              {!multiSelectMode && (
                <Popconfirm
                  title="确认删除此对话？"
                  onConfirm={e => {
                    e?.stopPropagation();
                    onDelete(session.session_id);
                  }}
                  onCancel={e => e?.stopPropagation()}
                >
                  <Button
                    type="text"
                    size="small"
                    danger
                    icon={<DeleteOutlined />}
                    onClick={e => e.stopPropagation()}
                    style={{ opacity: 0.4, fontSize: 12 }}
                  />
                </Popconfirm>
              )}
            </div>
          </Dropdown>
        )}
      </div>
    );
  };

  return (
    <div
      style={{
        width: SIDEBAR_WIDTH,
        height: '100%',
        borderRight: '1px solid #f0f0f0',
        display: 'flex',
        flexDirection: 'column',
        background: '#fafafa',
        flexShrink: 0,
      }}
    >
      {/* Header */}
      <div style={{ padding: '12px 16px', borderBottom: '1px solid #f0f0f0', display: 'flex', gap: 8 }}>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={onCreate}
          size="small"
          style={{ flex: 1 }}
        >
          新建对话
        </Button>
        <Tooltip title="收起侧栏">
          <Button
            type="text"
            size="small"
            icon={<MenuFoldOutlined />}
            onClick={() => setCollapsed(true)}
          />
        </Tooltip>
      </div>

      {/* Search */}
      <div style={{ padding: '8px 12px', borderBottom: '1px solid #f0f0f0' }}>
        <Search
          placeholder="搜索会话标题或内容"
          value={searchInput}
          onChange={e => handleSearchChange(e.target.value)}
          onSearch={value => {
            setSearchInput(value);
            setSearchKeyword(value);
          }}
          allowClear
          size="small"
          prefix={<SearchOutlined />}
          suffix={
            isSearchMode ? (
              <Badge count={sessions.length} style={{ backgroundColor: '#1677ff' }} />
            ) : null
          }
        />
      </div>

      {/* Multi-select toolbar */}
      {multiSelectMode && (
        <div
          style={{
            padding: '8px 12px',
            borderBottom: '1px solid #f0f0f0',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            background: '#f6ffed',
          }}
        >
          <Text type="secondary" style={{ fontSize: 12 }}>
            已选择 {selectedIds.size} 项
          </Text>
          <Space>
            <Button size="small" onClick={() => setSelectedIds(new Set())}>
              清空
            </Button>
            <Button size="small" onClick={toggleMultiSelect}>
              取消
            </Button>
            <Button
              size="small"
              type="primary"
              danger
              disabled={selectedIds.size === 0}
              loading={batchDeleteMut.isPending}
              onClick={handleBatchDelete}
            >
              删除选中
            </Button>
          </Space>
        </div>
      )}

      {/* Session List */}
      <div style={{ flex: 1, overflow: 'auto', padding: '8px' }}>
        {isLoading ? (
          <div style={{ textAlign: 'center', marginTop: 40 }}>
            <Spin size="small" />
          </div>
        ) : sessions.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={isSearchMode ? '无匹配结果' : '暂无对话'}
            style={{ marginTop: 40 }}
          />
        ) : (
          <>
            {isSearchMode ? (
              <div>{sessions.map(renderSessionItem)}</div>
            ) : (
              groups.map(group => (
                <div key={group.label} style={{ marginBottom: 12 }}>
                  <Text
                    type="secondary"
                    style={{ fontSize: 11, padding: '4px 8px', display: 'block' }}
                  >
                    {group.label}
                  </Text>
                  {group.items.map(renderSessionItem)}
                </div>
              ))
            )}

            {/* Load more */}
            {hasNextPage && (
              <div style={{ textAlign: 'center', padding: '12px 0' }}>
                <Button
                  size="small"
                  type="link"
                  loading={isFetchingNextPage}
                  onClick={() => currentQuery.fetchNextPage()}
                >
                  加载更多
                </Button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
