import { useEffect, useRef, useState, useCallback } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { Card, Input, Button, Tag, Space, Spin, Divider, Tooltip, Collapse } from 'antd';
import { SendOutlined, RobotOutlined, UserOutlined, ToolOutlined, DatabaseOutlined } from '@ant-design/icons';
import { agentApi, sessionsApi } from '../services/api';
import { useDeleteSession, useRenameSession } from '../hooks/useMemoryQueries';
import { useQueryClient } from '@tanstack/react-query';
import MarkdownRenderer from '../components/MarkdownRenderer';
import SessionSidebar from '../components/SessionSidebar';
import SummaryPanel from '../components/SummaryPanel';

const MEM_TYPE_COLORS: Record<string, string> = {
  info: 'blue',
  preference: 'green',
  plan: 'orange',
  fact: 'purple',
  event: 'cyan',
  skill: 'gold',
};

const MEM_TYPE_LABELS: Record<string, string> = {
  info: '信息',
  preference: '偏好',
  plan: '计划',
  fact: '事实',
  event: '事件',
  skill: '技能',
};

function PhaseIndicator({ phase }: { phase?: string }) {
  const phaseConfig: Record<string, { text: string; icon: string }> = {
    memory_recall: { text: '召回记忆中...', icon: '🔍' },
    llm_thinking: { text: '思考中...', icon: '🤔' },
    streaming: { text: '生成中...', icon: '✍️' },
  };
  const config = phaseConfig[phase || ''] || { text: '处理中...', icon: '⏳' };
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0', color: '#667eea' }}>
      <Spin size="small" />
      <span style={{ fontSize: 13 }}>{config.icon} {config.text}</span>
    </div>
  );
}

interface MemoryDetail {
  content: string;
  type: string;
  score: number;
}

interface ExtractionDetail {
  content: string;
  type: string;
}

interface Message {
  role: 'user' | 'assistant';
  content: string;
  toolCalls?: { tool: string; arguments: Record<string, unknown>; result: string }[];
  memoriesExtracted?: number;
  memoryContextUsed?: boolean;
  memoryDetails?: MemoryDetail[];
  extractionDetails?: ExtractionDetail[];
  loading?: boolean;
  phase?: 'memory_recall' | 'llm_thinking' | 'streaming' | 'done';
}

export default function AgentChatPage() {
  const queryClient = useQueryClient();
  const deleteSessionMut = useDeleteSession();
  const renameSessionMut = useRenameSession();

  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState<string | undefined>(undefined);
  const listRef = useRef<HTMLDivElement>(null);

  // 分页状态
  const PAGE_SIZE = 20;
  const [msgTotal, setMsgTotal] = useState(0);
  const [msgOffset, setMsgOffset] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);

  const refreshSessions = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['sessions'] });
  }, [queryClient]);

  const inputRef = useRef<HTMLTextAreaElement>(null);
  const prependingRef = useRef(false);

  const rowVirtualizer = useVirtualizer({
    count: messages.length,
    getScrollElement: () => listRef.current,
    estimateSize: () => 120,
    overscan: 8,
  });

  const scrollToBottom = useCallback(() => {
    if (messages.length === 0) return;
    requestAnimationFrame(() => {
      rowVirtualizer.scrollToIndex(messages.length - 1, { align: 'end' });
    });
  }, [messages.length, rowVirtualizer]);

  useEffect(() => {
    // 加载更早消息（前置插入）时不自动滚到底部
    if (prependingRef.current) {
      prependingRef.current = false;
      return;
    }
    scrollToBottom();
  }, [messages, scrollToBottom]);

  // 会话操作
  const handleSelectSession = async (sid: string) => {
    if (sid === sessionId) return;
    try {
      const res = await sessionsApi.messages(sid, PAGE_SIZE, 0);
      const msgs = (res.data.messages || []).map((m: any) => ({
        role: m.role as 'user' | 'assistant',
        content: m.content || '',
        toolCalls: m.tool_calls ? JSON.parse(m.tool_calls) : undefined,
      }));
      setMessages(msgs);
      setSessionId(sid);
      setMsgTotal(res.data.total || 0);
      setMsgOffset(PAGE_SIZE);
    } catch {
      // ignore
    }
  };

  // 加载更多历史消息
  const handleLoadMore = async () => {
    if (!sessionId || loadingMore) return;
    setLoadingMore(true);
    try {
      const res = await sessionsApi.messages(sessionId, PAGE_SIZE, msgOffset);
      const olderMsgs = (res.data.messages || []).map((m: any) => ({
        role: m.role as 'user' | 'assistant',
        content: m.content || '',
        toolCalls: m.tool_calls ? JSON.parse(m.tool_calls) : undefined,
      }));
      // 旧消息前置插入；标记 prepending，避免自动滚到底部
      prependingRef.current = true;
      setMessages(prev => [...olderMsgs, ...prev]);
      setMsgOffset(prev => prev + PAGE_SIZE);
      setMsgTotal(res.data.total || msgTotal);
      // 保持滚动位置：之前的顶部消息现在位于索引 olderMsgs.length
      if (olderMsgs.length > 0) {
        requestAnimationFrame(() => {
          rowVirtualizer.scrollToIndex(olderMsgs.length, { align: 'start' });
        });
      }
    } catch {
      // ignore
    } finally {
      setLoadingMore(false);
    }
  };

  const handleCreateSession = () => {
    setMessages([]);
    setSessionId(undefined);
    setMsgTotal(0);
    setMsgOffset(0);
  };

  const handleDeleteSession = async (sid: string) => {
    try {
      await deleteSessionMut.mutateAsync(sid);
      if (sid === sessionId) {
        setMessages([]);
        setSessionId(undefined);
      }
    } catch {
      // ignore
    }
  };

  const handleBatchDelete = (deletedIds: string[]) => {
    if (sessionId && deletedIds.includes(sessionId)) {
      setMessages([]);
      setSessionId(undefined);
      setMsgTotal(0);
      setMsgOffset(0);
    }
  };

  const handleRenameSession = async (sid: string, title: string) => {
    try {
      await renameSessionMut.mutateAsync({ sessionId: sid, title });
    } catch {
      // ignore
    }
  };

  const sendDirect = async (text: string) => {
    setInput(text);
    // Use setTimeout to ensure state updates before sending
    setTimeout(() => {
      const userMsg: Message = { role: 'user', content: text };
      setMessages(prev => [...prev, userMsg]);
      setInput('');
      setLoading(true);
      const loadingMsg: Message = { role: 'assistant', content: '', loading: true };
      setMessages(prev => [...prev, loadingMsg]);
      (async () => {
        try { await sendMessageStream(text); }
        catch { await sendMessageNonStream(text); }
        setLoading(false);
      })();
    }, 0);
  };

  const sendMessage = async () => {
    if (!input.trim() || loading) return;
    const text = input.trim();
    const userMsg: Message = { role: 'user', content: text };
    setMessages(prev => [...prev, userMsg]);
    setInput('');
    setLoading(true);
    const loadingMsg: Message = { role: 'assistant', content: '', loading: true };
    setMessages(prev => [...prev, loadingMsg]);
    try { await sendMessageStream(text); }
    catch { await sendMessageNonStream(text); }
    setLoading(false);
  };

  const sendMessageNonStream = async (text: string) => {
    const res = await agentApi.chat(text, undefined, sessionId);
    const data = res.data;
    setMessages(prev =>
      prev.map(m =>
        m.loading
          ? {
              role: 'assistant' as const,
              content: data.response || '无响应',
              toolCalls: data.tool_calls || [],
              memoriesExtracted: data.memories_extracted || 0,
              memoryContextUsed: data.memory_context_used || false,
            }
          : m
      )
    );
    if (data.session_id) setSessionId(data.session_id);
    refreshSessions();
  };

  const sendMessageStream = async (text: string): Promise<void> => {
    const response = await agentApi.chatStream(text, undefined, sessionId);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    const reader = response.body?.getReader();
    if (!reader) throw new Error('No reader available');

    const decoder = new TextDecoder();
    let assistantContent = '';
    let toolCalls: { tool: string; arguments: Record<string, unknown>; result: string }[] = [];
    let memoriesExtracted = 0;
    let memoryContextUsed = false;
    let memoryDetails: MemoryDetail[] = [];
    let extractionDetails: ExtractionDetail[] = [];
    let currentPhase: string | undefined;
    let newSessionId: string | undefined;
    let buffer = ''; // 缓冲区，处理跨 chunk 的 SSE 事件

    const updateLoadingMsg = (content: string) => {
      setMessages(prev =>
        prev.map(m =>
          m.loading
            ? {
                role: 'assistant' as const,
                content,
                loading: true,
                phase: currentPhase as Message['phase'],
                toolCalls: toolCalls.length > 0 ? [...toolCalls] : undefined,
                memoriesExtracted: memoriesExtracted || undefined,
                memoryContextUsed: memoryContextUsed || undefined,
                memoryDetails: memoryDetails.length > 0 ? memoryDetails : undefined,
                extractionDetails: extractionDetails.length > 0 ? extractionDetails : undefined,
              }
            : m
        )
      );
    };

    const processEvent = (data: string) => {
      try {
        const event = JSON.parse(data);
        const eventType = event.type;

        if (eventType === 'token') {
          currentPhase = 'streaming';
          assistantContent += event.content || '';
          updateLoadingMsg(assistantContent);
        } else if (eventType === 'phase') {
          currentPhase = event.phase;
          updateLoadingMsg(assistantContent);
        } else if (eventType === 'memory_context') {
          memoryContextUsed = true;
          memoryDetails = (event.memories || []).map((m: any) => ({
            content: m.content || '',
            type: m.type || 'info',
            score: m.score || 0,
          }));
          updateLoadingMsg(assistantContent);
        } else if (eventType === 'tool_call') {
          toolCalls.push({
            tool: event.tool || '',
            arguments: event.arguments || {},
            result: event.result || '',
          });
          updateLoadingMsg(assistantContent);
        } else if (eventType === 'memory') {
          memoriesExtracted = event.extracted || 0;
          memoryContextUsed = memoryContextUsed || event.context_used || false;
          if (event.details && Array.isArray(event.details)) {
            extractionDetails = event.details.map((d: any) => ({
              content: d.content || '',
              type: d.type || 'info',
            }));
          }
        } else if (eventType === 'done') {
          newSessionId = event.session_id;
        } else if (eventType === 'error') {
          console.error('SSE error:', event.message);
        }
      } catch (e) {
        console.warn('Failed to parse SSE event:', data, e);
      }
    };

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      const chunk = decoder.decode(value, { stream: true });
      buffer += chunk;

      // SSE 事件以 \n\n 分隔
      const events = buffer.split('\n\n');
      // 保留最后一个不完整的部分到缓冲区
      buffer = events.pop() || '';

      for (const event of events) {
        const lines = event.split('\n');
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            processEvent(line.slice(6));
          }
        }
      }
    }

    // 处理缓冲区中剩余的数据
    if (buffer.trim()) {
      const lines = buffer.split('\n');
      for (const line of lines) {
        if (line.startsWith('data: ')) {
          processEvent(line.slice(6));
        }
      }
    }

    // Final update with complete data
    setMessages(prev =>
      prev.map(m =>
        m.loading
          ? {
              role: 'assistant' as const,
              content: assistantContent || '无响应',
              phase: 'done' as const,
              toolCalls: toolCalls.length > 0 ? toolCalls : undefined,
              memoriesExtracted: memoriesExtracted || undefined,
              memoryContextUsed: memoryContextUsed || undefined,
              memoryDetails: memoryDetails.length > 0 ? memoryDetails : undefined,
              extractionDetails: extractionDetails.length > 0 ? extractionDetails : undefined,
            }
          : m
      )
    );
    if (newSessionId) setSessionId(newSessionId);
    // 刷新会话列表和摘要
    refreshSessions();
    if (newSessionId || sessionId) {
      queryClient.invalidateQueries({ queryKey: ['sessions', newSessionId || sessionId, 'summary'] });
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  const quickActions = [
    { label: '记住我的信息', msg: '你好，我叫鑫海，在腾讯工作，是一名产品经理' },
    { label: '测试记忆召回', msg: '你还记得我的名字和职业吗？' },
    { label: '查询所有记忆', msg: '帮我查一下我之前告诉过你的所有信息' },
  ];

  return (
    <div style={{ display: 'flex', height: 'calc(100vh - 96px)', width: '100%', overflow: 'hidden' }}>
      {/* 左侧会话列表 */}
      <SessionSidebar
        activeSessionId={sessionId}
        onSelect={handleSelectSession}
        onDelete={handleDeleteSession}
        onRename={handleRenameSession}
        onCreate={handleCreateSession}
        onBatchDelete={handleBatchDelete}
      />

      {/* 摘要面板（在会话列表和对话区之间） */}
      <SummaryPanel sessionId={sessionId} />

      {/* 右侧对话区域 */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
      {messages.length === 0 && (
        <Card style={{ margin: '0 24px 16px', flexShrink: 0 }}>
          <p style={{ color: '#888', marginBottom: 12 }}>快速开始：点击以下示例消息开始对话</p>
          <Space wrap>
            {quickActions.map(a => (
              <Button key={a.msg} onClick={() => sendDirect(a.msg)} disabled={loading}>
                {a.label}
              </Button>
            ))}
          </Space>
        </Card>
      )}

      {/* 分页：加载更早消息（置于虚拟滚动区上方，避免影响虚拟测量） */}
      {sessionId && msgOffset < msgTotal && (
        <div style={{ textAlign: 'center', padding: '8px 0', flexShrink: 0 }}>
          <Button
            size="small"
            type="link"
            loading={loadingMore}
            onClick={handleLoadMore}
          >
            加载更早的消息 ({msgOffset}/{msgTotal})
          </Button>
        </div>
      )}
      {sessionId && msgTotal > 0 && msgOffset >= msgTotal && (
        <div style={{ textAlign: 'center', padding: '6px 0', color: '#ccc', fontSize: 12, flexShrink: 0 }}>
          已加载全部 {msgTotal} 条消息
        </div>
      )}
      <div
        ref={listRef}
        style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden', padding: '0 24px', minHeight: 0 }}
      >
        <div style={{ height: rowVirtualizer.getTotalSize(), width: '100%', position: 'relative' }}>
          {rowVirtualizer.getVirtualItems().map((virtualRow) => {
            const msg = messages[virtualRow.index];
            return (
            <div
              key={virtualRow.key}
              data-index={virtualRow.index}
              ref={rowVirtualizer.measureElement}
              style={{
                position: 'absolute',
                top: 0,
                left: 0,
                width: '100%',
                transform: `translateY(${virtualRow.start}px)`,
              }}
            >
            <div
              style={{
                display: 'flex',
                justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start',
                marginBottom: 16,
              }}
            >
              <Card
                size="small"
                style={{
                  maxWidth: '75%',
                  borderRadius: msg.role === 'user' ? '12px 12px 2px 12px' : '12px 12px 12px 2px',
                  background: msg.role === 'user' ? '#e6f4ff' : '#fff',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                  {msg.role === 'assistant' ? <RobotOutlined style={{ color: '#667eea' }} /> : <UserOutlined />}
                  <span style={{ fontWeight: 600, fontSize: 13 }}>
                    {msg.role === 'assistant' ? 'Agent' : '我'}
                  </span>
                  {msg.memoryContextUsed && (
                    <Tooltip title="使用了记忆上下文">
                      <Tag color="blue" icon={<DatabaseOutlined />} style={{ margin: 0, fontSize: 11 }}>
                        记忆
                      </Tag>
                    </Tooltip>
                  )}
                  {msg.memoriesExtracted !== undefined && msg.memoriesExtracted > 0 && (
                    <Tag color="green" style={{ margin: 0, fontSize: 11 }}>
                      +{msg.memoriesExtracted} 记忆
                    </Tag>
                  )}
                </div>

                {msg.loading && !msg.content ? (
                  <PhaseIndicator phase={msg.phase} />
                ) : (
                  msg.role === 'assistant' ? (
                    <MarkdownRenderer content={msg.content} streaming={msg.loading} />
                  ) : (
                    <div style={{ whiteSpace: 'pre-wrap', lineHeight: 1.6 }}>{msg.content}</div>
                  )
                )}

                {msg.toolCalls && msg.toolCalls.length > 0 && (
                  <>
                    <Divider style={{ margin: '8px 0' }} />
                    <Collapse
                      ghost
                      size="small"
                      items={[{
                        key: 'tools',
                        label: (
                          <span style={{ fontSize: 12, color: '#888' }}>
                            <ToolOutlined /> 工具调用 ({msg.toolCalls!.length})
                          </span>
                        ),
                        children: (
                          <div>
                            {msg.toolCalls!.map((tc, i) => (
                              <div
                                key={i}
                                style={{
                                  background: '#f5f5f5',
                                  borderRadius: 4,
                                  padding: '4px 8px',
                                  marginTop: 4,
                                  fontSize: 12,
                                }}
                              >
                                <Tag color="purple" style={{ margin: 0, fontSize: 11 }}>
                                  {tc.tool}
                                </Tag>
                                <span style={{ color: '#666', marginLeft: 4 }}>
                                  {JSON.stringify(tc.arguments)}
                                </span>
                              </div>
                            ))}
                          </div>
                        ),
                      }]}
                    />
                  </>
                )}

                {/* Memory Context Panel */}
                {msg.memoryDetails && msg.memoryDetails.length > 0 && (
                  <>
                    <Divider style={{ margin: '8px 0' }} />
                    <Collapse
                      ghost
                      size="small"
                      items={[{
                        key: 'memory',
                        label: (
                          <span style={{ fontSize: 12, color: '#1677ff' }}>
                            <DatabaseOutlined /> 本次回复参考了 {msg.memoryDetails!.length} 条记忆
                          </span>
                        ),
                        children: (
                          <div>
                            {msg.memoryDetails!.map((m, i) => (
                              <div key={i} style={{ padding: '3px 0', fontSize: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
                                <Tag
                                  color={MEM_TYPE_COLORS[m.type] || 'default'}
                                  style={{ margin: 0, fontSize: 11, minWidth: 48, textAlign: 'center' }}
                                >
                                  {MEM_TYPE_LABELS[m.type] || m.type}
                                </Tag>
                                <span style={{ flex: 1, color: '#444' }}>{m.content}</span>
                                <span style={{ color: '#aaa', fontSize: 11 }}>相关度: {m.score.toFixed(2)}</span>
                              </div>
                            ))}
                          </div>
                        ),
                      }]}
                    />
                  </>
                )}

                {/* Memory Extraction Feedback */}
                {msg.memoriesExtracted !== undefined && msg.memoriesExtracted > 0 && (
                  <>
                    <Divider style={{ margin: '8px 0' }} />
                    <div style={{ fontSize: 12, color: '#52c41a' }}>
                      💾 已从对话中提取 {msg.memoriesExtracted} 条新记忆
                    </div>
                    {msg.extractionDetails && msg.extractionDetails.length > 0 && (
                      <Collapse
                        ghost
                        size="small"
                        items={[{
                          key: 'extraction',
                          label: <span style={{ fontSize: 12, color: '#52c41a' }}>查看详情</span>,
                          children: (
                            <div>
                              {msg.extractionDetails!.map((e, i) => (
                                <div key={i} style={{ padding: '3px 0', fontSize: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
                                  <Tag color="green" style={{ margin: 0, fontSize: 11 }}>{e.type}</Tag>
                                  <span style={{ color: '#444' }}>{e.content}</span>
                                </div>
                              ))}
                            </div>
                          ),
                        }]}
                      />
                    )}
                  </>
                )}
              </Card>
            </div>
            </div>
            );
          })}
        </div>
      </div>

      <div style={{ padding: '16px 24px', flexShrink: 0 }}>
        <Card size="small">
          <Space.Compact style={{ width: '100%' }}>
            <Input.TextArea
              ref={inputRef as any}
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="输入消息... (Enter 发送, Shift+Enter 换行)"
              autoSize={{ minRows: 1, maxRows: 4 }}
              disabled={loading}
              style={{ resize: 'none' }}
            />
            <Button
              type="primary"
              icon={<SendOutlined />}
              onClick={sendMessage}
              loading={loading}
              disabled={!input.trim()}
              style={{ height: 'auto' }}
            >
              发送
            </Button>
          </Space.Compact>
        </Card>
      </div>
      </div>
    </div>
  );
}
