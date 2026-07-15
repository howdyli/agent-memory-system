import { useState, useEffect, useCallback } from 'react';
import {
  Collapse,
  Input,
  Button,
  Space,
  Rate,
  Alert,
  List,
  Tooltip,
  Modal,
  Typography,
  Tag,
} from 'antd';
import {
  FileTextOutlined,
  SaveOutlined,
  ReloadOutlined,
  HistoryOutlined,
  ExclamationCircleOutlined,
} from '@ant-design/icons';
import {
  useSessionSummary,
  useSessionSummaryHistory,
  useUpdateSessionSummary,
  useRegenerateSummary,
} from '../hooks/useMemoryQueries';

const { Text } = Typography;
const { TextArea } = Input;

interface SummaryPanelProps {
  sessionId?: string;
}

const PANEL_WIDTH = 320;
const PANEL_COLLAPSED_WIDTH = 40;

export default function SummaryPanel({ sessionId }: SummaryPanelProps) {
  const [expanded, setExpanded] = useState(false);
  const [editingSummary, setEditingSummary] = useState('');
  const [isDirty, setIsDirty] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);

  // React Query hooks
  const { data: summaryData, isLoading: summaryLoading } = useSessionSummary(
    expanded ? sessionId : undefined
  );
  const { data: historyData, isLoading: historyLoading, refetch: refetchHistory } = useSessionSummaryHistory(
    expanded && historyOpen ? sessionId : undefined
  );
  const updateSummaryMut = useUpdateSessionSummary();
  const regenerateMut = useRegenerateSummary();

  // Sync editing text with fetched data
  useEffect(() => {
    if (summaryData?.summary) {
      setEditingSummary(summaryData.summary);
      setIsDirty(false);
    } else {
      setEditingSummary('');
      setIsDirty(false);
    }
  }, [summaryData?.summary]);

  const handleSave = useCallback(async () => {
    if (!sessionId || !isDirty) return;
    try {
      await updateSummaryMut.mutateAsync({ sessionId, summary: editingSummary });
      setIsDirty(false);
    } catch {
      // handled by global error handler
    }
  }, [sessionId, editingSummary, isDirty, updateSummaryMut]);

  const handleRegenerate = useCallback(() => {
    if (!sessionId) return;
    Modal.confirm({
      title: '重新生成摘要',
      icon: <ExclamationCircleOutlined />,
      content: '将覆盖当前摘要内容，是否继续？',
      okText: '确认',
      cancelText: '取消',
      onOk: async () => {
        try {
          const result = await regenerateMut.mutateAsync(sessionId);
          if (result.data?.summary) {
            setEditingSummary(result.data.summary);
            setIsDirty(false);
          }
        } catch {
          // handled by global error handler
        }
      },
    });
  }, [sessionId, regenerateMut]);

  const handleHistoryToggle = useCallback(() => {
    if (!historyOpen && sessionId) {
      refetchHistory();
    }
    setHistoryOpen(prev => !prev);
  }, [historyOpen, sessionId, refetchHistory]);

  const handleRestoreVersion = useCallback((summary: string) => {
    setEditingSummary(summary);
    setIsDirty(true);
    setHistoryOpen(false);
  }, []);

  const qualityScore = summaryData?.quality?.score ?? 0;
  const qualityDetails = summaryData?.quality?.details;
  const updatedAt = summaryData?.updated_at;
  const historyCount = summaryData?.history_count ?? 0;

  // Collapsed state - show thin sidebar with icon
  if (!expanded) {
    return (
      <div
        style={{
          width: PANEL_COLLAPSED_WIDTH,
          height: '100%',
          borderRight: '1px solid #f0f0f0',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          background: '#fafafa',
          flexShrink: 0,
        }}
      >
        <Tooltip title="展开摘要面板" placement="right">
          <Button
            type="text"
            icon={<FileTextOutlined />}
            onClick={() => setExpanded(true)}
            style={{ marginTop: 12, fontSize: 16, color: '#667eea' }}
          />
        </Tooltip>
        {summaryData?.summary && (
          <Tooltip title="已有摘要" placement="right">
            <div
              style={{
                width: 8,
                height: 8,
                borderRadius: '50%',
                background: '#52c41a',
                marginTop: 8,
              }}
            />
          </Tooltip>
        )}
      </div>
    );
  }

  // Expanded state
  return (
    <div
      style={{
        width: PANEL_WIDTH,
        height: '100%',
        borderRight: '1px solid #f0f0f0',
        display: 'flex',
        flexDirection: 'column',
        background: '#fafafa',
        flexShrink: 0,
        overflow: 'hidden',
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: '12px 16px',
          borderBottom: '1px solid #f0f0f0',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexShrink: 0,
        }}
      >
        <Space>
          <FileTextOutlined style={{ color: '#667eea' }} />
          <Text strong style={{ fontSize: 14 }}>对话摘要</Text>
        </Space>
        <Button
          type="text"
          size="small"
          onClick={() => setExpanded(false)}
          style={{ color: '#999' }}
        >
          收起
        </Button>
      </div>

      {/* Content */}
      <div style={{ flex: 1, overflow: 'auto', padding: '12px 16px' }}>
        {!sessionId ? (
          <Alert
            message="请先选择一个会话"
            type="info"
            showIcon
            style={{ marginTop: 16 }}
          />
        ) : summaryLoading ? (
          <div style={{ textAlign: 'center', marginTop: 32, color: '#999' }}>
            加载中...
          </div>
        ) : (
          <>
            {/* Quality Score */}
            <div style={{ marginBottom: 12 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                <Text type="secondary" style={{ fontSize: 12 }}>质量评分</Text>
                <Rate
                  disabled
                  value={qualityScore}
                  allowHalf
                  style={{ fontSize: 14 }}
                />
                <Tag color={qualityScore >= 4 ? 'green' : qualityScore >= 2.5 ? 'orange' : 'red'} style={{ fontSize: 11 }}>
                  {qualityScore.toFixed(1)}
                </Tag>
              </div>
              {qualityDetails && (
                <div style={{ display: 'flex', gap: 8, fontSize: 11, color: '#999' }}>
                  <span>长度: {qualityDetails.length}/5</span>
                  <span>密度: {qualityDetails.density}/5</span>
                  <span>结构: {qualityDetails.structure}/5</span>
                </div>
              )}
            </div>

            {/* Updated time */}
            {updatedAt && (
              <div style={{ fontSize: 11, color: '#999', marginBottom: 8 }}>
                最后更新: {new Date(updatedAt).toLocaleString('zh-CN')}
              </div>
            )}

            {/* Editable Summary */}
            <div style={{ marginBottom: 12 }}>
              <TextArea
                value={editingSummary}
                onChange={e => {
                  setEditingSummary(e.target.value);
                  setIsDirty(true);
                }}
                placeholder="暂无摘要。可点击下方「重新生成」按钮自动生成。"
                autoSize={{ minRows: 4, maxRows: 12 }}
                style={{ fontSize: 13, lineHeight: 1.6 }}
              />
              <div style={{ fontSize: 11, color: '#bbb', marginTop: 2, textAlign: 'right' }}>
                {editingSummary.length} 字
              </div>
            </div>

            {/* Action Buttons */}
            <Space style={{ marginBottom: 16 }}>
              <Button
                type={isDirty ? 'primary' : 'default'}
                icon={<SaveOutlined />}
                loading={updateSummaryMut.isPending}
                disabled={!isDirty || !sessionId}
                onClick={handleSave}
                size="small"
              >
                保存
              </Button>
              <Button
                icon={<ReloadOutlined />}
                loading={regenerateMut.isPending}
                disabled={!sessionId}
                onClick={handleRegenerate}
                size="small"
              >
                重新生成
              </Button>
            </Space>

            {/* History */}
            {historyCount > 1 && (
              <Collapse
                ghost
                size="small"
                activeKey={historyOpen ? ['history'] : []}
                onChange={() => handleHistoryToggle()}
                items={[{
                  key: 'history',
                  label: (
                    <span style={{ fontSize: 12, color: '#667eea' }}>
                      <HistoryOutlined /> 历史版本 ({historyCount})
                    </span>
                  ),
                  children: historyLoading ? (
                    <div style={{ textAlign: 'center', padding: 8, color: '#999' }}>
                      加载中...
                    </div>
                  ) : (
                    <List
                      size="small"
                      dataSource={historyData?.history || []}
                      renderItem={(item: any, index: number) => (
                        <List.Item
                          style={{ padding: '8px 0', cursor: 'pointer' }}
                          onClick={() => handleRestoreVersion(item.summary)}
                        >
                          <div style={{ width: '100%' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginBottom: 2 }}>
                              {index === 0 && <Tag color="blue" style={{ fontSize: 10, margin: 0 }}>当前</Tag>}
                              <Text type="secondary" style={{ fontSize: 11 }}>
                                {item.created_at ? new Date(item.created_at).toLocaleString('zh-CN') : ''}
                              </Text>
                              <Rate
                                disabled
                                value={item.quality?.score ?? 0}
                                allowHalf
                                style={{ fontSize: 10 }}
                              />
                            </div>
                            <div
                              style={{
                                fontSize: 12,
                                color: '#666',
                                overflow: 'hidden',
                                textOverflow: 'ellipsis',
                                display: '-webkit-box',
                                WebkitLineClamp: 2,
                                WebkitBoxOrient: 'vertical',
                              }}
                            >
                              {item.summary}
                            </div>
                          </div>
                        </List.Item>
                      )}
                    />
                  ),
                }]}
              />
            )}

            {/* Notice */}
            <Alert
              message="保存仅更新 SQLite 记录，不会触发 ChromaDB 向量重建"
              type="info"
              showIcon
              style={{ fontSize: 11, marginTop: 8 }}
            />
          </>
        )}
      </div>
    </div>
  );
}
