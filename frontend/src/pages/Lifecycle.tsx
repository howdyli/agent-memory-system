import { useEffect, useMemo, useState } from 'react';
import { Card, Row, Col, Statistic, Table, Tag, Tabs, Input, Button, InputNumber, Space, message, Popconfirm, Descriptions, Select, Modal, Timeline, DatePicker } from 'antd';
import {
  ExperimentOutlined, DeleteOutlined, RestOutlined,
  ReloadOutlined, ThunderboltOutlined, WarningOutlined,
  MergeCellsOutlined, FileTextOutlined, CheckCircleOutlined,
  ClockCircleOutlined, SearchOutlined
} from '@ant-design/icons';
import dayjs from 'dayjs';
import { lifecycleApi } from '../services/api';
import { useLifecycleConflicts, useLifecycleMergeLog, useDetectConflict, useResolveConflict } from '../hooks/useMemoryQueries';

export default function LifecyclePage() {
  const [stats, setStats] = useState<Record<string, any> | null>(null);
  const [coldList, setColdList] = useState<any[]>([]);
  const [deletedList, setDeletedList] = useState<any[]>([]);
  const [duplicates, setDuplicates] = useState<any[]>([]);
  const [loading, setLoading] = useState<Record<string, boolean>>({});

  // Mark cold
  const [markType, setMarkType] = useState('fragment');
  const [markId, setMarkId] = useState('');

  // Archive
  const [archiveType, setArchiveType] = useState('fragment');
  const [archiveId, setArchiveId] = useState('');

  // Duplicate detection
  const [mergeThreshold, setMergeThreshold] = useState(0.8);
  const [mergeContent, setMergeContent] = useState('');

  // Conflict detection inputs
  const [conflictKey, setConflictKey] = useState('');
  const [conflictValue, setConflictValue] = useState('');

  // Conflict resolution filters & modal
  const [typeFilter, setTypeFilter] = useState<string | null>(null);
  const [dateRange, setDateRange] = useState<[dayjs.Dayjs | null, dayjs.Dayjs | null] | null>(null);
  const [resolveModalOpen, setResolveModalOpen] = useState(false);
  const [selectedConflict, setSelectedConflict] = useState<any>(null);
  const [manualValue, setManualValue] = useState('');
  const [resolutionMode, setResolutionMode] = useState<'accept_new' | 'keep_current' | 'manual' | null>(null);

  const setLoad = (key: string, val: boolean) => setLoading(prev => ({ ...prev, [key]: val }));

  // React Query hooks for conflicts
  const { data: conflicts = [], isLoading: conflictsLoading, refetch: refetchConflicts } = useLifecycleConflicts();
  const { data: mergeLog = [], isLoading: mergeLogLoading, refetch: refetchMergeLog } = useLifecycleMergeLog();
  const detectConflictMutation = useDetectConflict();
  const resolveConflictMutation = useResolveConflict();

  useEffect(() => {
    fetchStats();
    fetchColdList();
    fetchDeletedList();
  }, []);

  const fetchStats = async () => {
    setLoad('stats', true);
    try { const res = await lifecycleApi.stats(); setStats(res.data); } catch { /* */ }
    setLoad('stats', false);
  };

  const fetchColdList = async () => {
    setLoad('cold', true);
    try { const res = await lifecycleApi.coldList(); setColdList(res.data?.memories || res.data || []); } catch { /* */ }
    setLoad('cold', false);
  };

  const fetchDeletedList = async () => {
    setLoad('deleted', true);
    try { const res = await lifecycleApi.deletedList(); setDeletedList(res.data?.memories || res.data || []); } catch { /* */ }
    setLoad('deleted', false);
  };

  const handleMarkCold = async () => {
    if (!markId.trim()) return;
    try { await lifecycleApi.markCold({ memory_type: markType, memory_id: markId.trim() }); message.success('已标记为冷记忆'); setMarkId(''); fetchColdList(); }
    catch { message.error('标记失败'); }
  };

  const handleRestore = async (type: string, id: string) => {
    try { await lifecycleApi.restore(type, id); message.success('已恢复'); fetchDeletedList(); } catch { message.error('恢复失败'); }
  };

  const handleHardDelete = async (type: string, id: string) => {
    try { await lifecycleApi.hardDelete(type, id); message.success('已硬删除'); fetchDeletedList(); } catch { message.error('删除失败'); }
  };

  const handleArchive = async () => {
    if (!archiveId.trim()) return;
    try { await lifecycleApi.archive(archiveType, archiveId.trim()); message.success('已归档'); setArchiveId(''); } catch { message.error('归档失败'); }
  };

  const handleFindDuplicates = async () => {
    if (!mergeContent.trim()) { message.warning('请输入要检测的内容'); return; }
    setLoad('duplicates', true);
    try { const res = await lifecycleApi.findDuplicates(mergeContent, mergeThreshold); setDuplicates(res.data?.duplicates || res.data?.pairs || res.data || []); } catch { message.error('查找失败'); }
    setLoad('duplicates', false);
  };

  const handleMerge = async (sourceIds: number[], targetContent: string) => {
    try { await lifecycleApi.mergeMemories(sourceIds, targetContent); message.success('合并成功'); handleFindDuplicates(); } catch { message.error('合并失败'); }
  };

  const handleDetectConflicts = async () => {
    if (!conflictKey.trim() || !conflictValue.trim()) { message.warning('请输入 key 和 new_value'); return; }
    try {
      const res = await detectConflictMutation.mutateAsync({ key: conflictKey.trim(), new_value: conflictValue.trim() });
      if (res.data?.conflict) {
        message.warning(res.data?.message || '检测到冲突');
      } else {
        message.success(res.data?.message || '无冲突');
      }
    } catch { message.error('检测失败'); }
  };

  const openResolveModal = (record: any) => {
    setSelectedConflict(record);
    setResolutionMode(null);
    setManualValue(`${record.old_value || ''} / ${record.new_value || ''}`);
    setResolveModalOpen(true);
  };

  const closeResolveModal = () => {
    setResolveModalOpen(false);
    setSelectedConflict(null);
    setResolutionMode(null);
    setManualValue('');
  };

  const handleResolve = async () => {
    if (!selectedConflict || !resolutionMode) {
      message.warning('请选择解决方式');
      return;
    }
    const mergedValue = resolutionMode === 'manual' ? manualValue : undefined;
    try {
      await resolveConflictMutation.mutateAsync({
        conflictId: selectedConflict.id,
        resolution: resolutionMode,
        mergedValue,
      });
      message.success('冲突已解决');
      closeResolveModal();
    } catch { message.error('解决失败'); }
  };

  const filteredConflicts = useMemo(() => {
    return (conflicts as any[]).filter((c) => {
      if (typeFilter && (c.conflict_type || 'value_mismatch') !== typeFilter) return false;
      if (dateRange && dateRange[0] && dateRange[1] && c.created_at) {
        const t = dayjs(c.created_at);
        if (!t.isValid() || t.isBefore(dateRange[0]) || t.isAfter(dateRange[1].endOf('day'))) return false;
      }
      return true;
    });
  }, [conflicts, typeFilter, dateRange]);

  const getConflictKey = (record: any) => {
    if (record.target_id) return record.target_id;
    try {
      const ids = JSON.parse(record.source_ids || '[]');
      return ids[0] || record.source_ids;
    } catch {
      return record.source_ids;
    }
  };

  const conflictTypeMap: Record<string, string> = {
    value_mismatch: '值不一致',
    source_conflict: '来源冲突',
    time_conflict: '时间冲突',
  };

  const conflictTypeColor: Record<string, string> = {
    value_mismatch: 'orange',
    source_conflict: 'blue',
    time_conflict: 'purple',
  };

  const resolvedHistory = useMemo(() => {
    return (mergeLog as any[]).filter(
      (log) => log.merge_type === 'conflict' && log.resolved && String(log.merge_action || '').startsWith('resolved:')
    );
  }, [mergeLog]);

  return (
    <div>
      <div className="page-header">
        <h2><ExperimentOutlined /> 生命周期</h2>
        <p>记忆冷热管理、回收站、重复合并与冲突检测</p>
      </div>

      <Tabs defaultActiveKey="overview" items={[
        // ======== Tab 1: Overview ========
        {
          key: 'overview', label: <span><FileTextOutlined /> 概览</span>,
          children: (
            <div>
              <Row gutter={16} style={{ marginBottom: 24 }}>
                <Col span={6}><Card loading={loading.stats}><Statistic title="总记忆数" value={stats?.total_memories ?? '-'} prefix={<FileTextOutlined />} /></Card></Col>
                <Col span={6}><Card loading={loading.stats}><Statistic title="活跃记忆" value={stats?.active_memories ?? '-'} prefix={<CheckCircleOutlined />} valueStyle={{ color: '#52c41a' }} /></Card></Col>
                <Col span={6}><Card loading={loading.stats}><Statistic title="冷记忆" value={stats?.cold_memories ?? coldList.length} prefix={<ClockCircleOutlined />} valueStyle={{ color: '#13c2c2' }} /></Card></Col>
                <Col span={6}><Card loading={loading.stats}><Statistic title="已删除" value={stats?.deleted_memories ?? deletedList.length} prefix={<DeleteOutlined />} valueStyle={{ color: '#ff4d4f' }} /></Card></Col>
              </Row>
              <Row gutter={16}>
                <Col span={8}>
                  <Card title="冷记忆列表" size="small" extra={<Button size="small" icon={<ReloadOutlined />} onClick={fetchColdList}>刷新</Button>}>
                    <Table dataSource={coldList.slice(0, 5)} rowKey={(r) => r.id || Math.random()} size="small" pagination={false} loading={loading.cold}
                      columns={[
                        { title: 'ID', dataIndex: 'memory_id', width: 100, ellipsis: true },
                        { title: '类型', dataIndex: 'memory_type', width: 70 },
                        { title: '状态', dataIndex: 'lifecycle_status', width: 80, render: (s: string) => <Tag color="cyan">{s}</Tag> },
                        { title: '最后召回', dataIndex: 'last_recalled_at', width: 120, render: (v: string) => v ? new Date(v).toLocaleDateString() : '-' },
                      ]} />
                  </Card>
                </Col>
                <Col span={8}>
                  <Card title="已删除记忆" size="small" extra={<Button size="small" icon={<ReloadOutlined />} onClick={fetchDeletedList}>刷新</Button>}>
                    <Table dataSource={deletedList.slice(0, 5)} rowKey={(r) => r.id || Math.random()} size="small" pagination={false} loading={loading.deleted}
                      columns={[
                        { title: 'ID', dataIndex: 'memory_id', width: 100, ellipsis: true },
                        { title: '类型', dataIndex: 'memory_type', width: 70 },
                        { title: '删除时间', dataIndex: 'deleted_at', width: 120, render: (v: string) => v ? new Date(v).toLocaleDateString() : '-' },
                      ]} />
                  </Card>
                </Col>
                <Col span={8}>
                  <Card title="半衰期配置" size="small">
                    {['info', 'preference', 'plan', 'fact', 'summary'].map(t => (
                      <div key={t} style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0', borderBottom: '1px solid #f0f0f0' }}>
                        <Tag>{t}</Tag>
                        <Button type="link" size="small" onClick={async () => {
                          try { const res = await lifecycleApi.halfLife(t); message.info(`${t}: ${res.data?.half_life_days || res.data} 天`); } catch { /* */ }
                        }}>查看</Button>
                      </div>
                    ))}
                  </Card>
                </Col>
              </Row>
            </div>
          ),
        },

        // ======== Tab 2: Cold Memory ========
        {
          key: 'cold', label: <span><ClockCircleOutlined /> 冷记忆</span>,
          children: (
            <Card title="冷记忆管理" extra={
              <Space>
                <Button icon={<ReloadOutlined />} onClick={fetchColdList}>刷新</Button>
              </Space>
            }>
              <div style={{ marginBottom: 16, padding: 16, background: '#fafafa', borderRadius: 6 }}>
                <Space>
                  <Select value={markType} onChange={setMarkType} style={{ width: 120 }} options={[{ value: 'fragment', label: '片段' }, { value: 'variable', label: '变量' }]} />
                  <Input value={markId} onChange={e => setMarkId(e.target.value)} placeholder="memory_id..." style={{ width: 200 }} />
                  <Button type="primary" onClick={handleMarkCold}>标记为冷记忆</Button>
                </Space>
              </div>
              <Table dataSource={coldList} rowKey={(r) => r.id || Math.random()} size="small" loading={loading.cold}
                columns={[
                  { title: '记忆 ID', dataIndex: 'memory_id', ellipsis: true },
                  { title: '类型', dataIndex: 'memory_type', width: 80 },
                  { title: '状态', dataIndex: 'lifecycle_status', width: 80, render: (s: string) => <Tag color="cyan">{s}</Tag> },
                  { title: '重要性', dataIndex: 'importance_score', width: 80, render: (v: number) => v?.toFixed(2) || '-' },
                  { title: '最后召回', dataIndex: 'last_recalled_at', width: 130, render: (v: string) => v ? new Date(v).toLocaleString() : '-' },
                  { title: '创建时间', dataIndex: 'created_at', width: 130, render: (v: string) => v ? new Date(v).toLocaleString() : '-' },
                  { title: '操作', width: 120, render: (_: any, r: any) => (
                    <Space>
                      <Popconfirm title="恢复此记忆？" onConfirm={() => handleRestore(r.memory_type || 'fragment', r.memory_id)}>
                        <Button size="small" icon={<RestOutlined />}>恢复</Button>
                      </Popconfirm>
                    </Space>
                  )},
                ]} />
            </Card>
          ),
        },

        // ======== Tab 3: Deleted (Recycle Bin) ========
        {
          key: 'deleted', label: <span><DeleteOutlined /> 回收站</span>,
          children: (
            <Card title="已删除记忆" extra={<Button icon={<ReloadOutlined />} onClick={fetchDeletedList}>刷新</Button>}>
              <Table dataSource={deletedList} rowKey={(r) => r.id || Math.random()} size="small" loading={loading.deleted}
                columns={[
                  { title: '记忆 ID', dataIndex: 'memory_id', ellipsis: true },
                  { title: '类型', dataIndex: 'memory_type', width: 80 },
                  { title: '状态', dataIndex: 'lifecycle_status', width: 80, render: (s: string) => <Tag color="red">{s}</Tag> },
                  { title: '删除原因', dataIndex: 'delete_reason', ellipsis: true },
                  { title: '删除时间', dataIndex: 'deleted_at', width: 130, render: (v: string) => v ? new Date(v).toLocaleString() : '-' },
                  { title: '操作', width: 160, render: (_: any, r: any) => (
                    <Space>
                      <Button size="small" type="primary" icon={<RestOutlined />} onClick={() => handleRestore(r.memory_type || 'fragment', r.memory_id)}>恢复</Button>
                      <Popconfirm title="永久删除？不可恢复！" onConfirm={() => handleHardDelete(r.memory_type || 'fragment', r.memory_id)}>
                        <Button size="small" danger icon={<DeleteOutlined />}>删除</Button>
                      </Popconfirm>
                    </Space>
                  )},
                ]} />
            </Card>
          ),
        },

        // ======== Tab 4: Archive ========
        {
          key: 'archive', label: <span><ExperimentOutlined /> 归档</span>,
          children: (
            <Row gutter={16}>
              <Col span={12}>
                <Card title="手动归档" size="small" style={{ marginBottom: 16 }}>
                  <Space>
                    <Select value={archiveType} onChange={setArchiveType} style={{ width: 120 }} options={[{ value: 'fragment', label: '片段' }, { value: 'variable', label: '变量' }]} />
                    <Input value={archiveId} onChange={e => setArchiveId(e.target.value)} placeholder="memory_id..." style={{ width: 200 }} />
                    <Button type="primary" onClick={handleArchive}>归档</Button>
                  </Space>
                </Card>
              </Col>
              <Col span={12}>
                <Card title="批量操作" size="small" style={{ marginBottom: 16 }}>
                  <Space>
                    <Button icon={<ExperimentOutlined />} onClick={async () => { try { await lifecycleApi.autoArchive(); message.success('自动归档完成'); } catch { message.error('归档失败'); } }}>自动归档</Button>
                    <Button icon={<ThunderboltOutlined />} onClick={async () => { try { await lifecycleApi.runCleanup(); message.success('清理完成'); } catch { message.error('清理失败'); } }}>运行清理</Button>
                  </Space>
                </Card>
              </Col>
            </Row>
          ),
        },

        // ======== Tab 5: Duplicates ========
        {
          key: 'duplicates', label: <span><MergeCellsOutlined /> 重复合并</span>,
          children: (
            <Card title="查找与合并重复记忆"
              extra={
                <Space>
                  <span>阈值:</span>
                  <InputNumber min={0.5} max={1} step={0.05} value={mergeThreshold} onChange={v => setMergeThreshold(v || 0.8)} style={{ width: 80 }} />
                  <Button type="primary" icon={<SearchOutlined />} onClick={handleFindDuplicates} loading={loading.duplicates}>查找重复</Button>
                </Space>
              }>
              <div style={{ marginBottom: 16, padding: 16, background: '#fafafa', borderRadius: 6 }}>
                <Input.TextArea rows={2} value={mergeContent} onChange={e => setMergeContent(e.target.value)}
                  placeholder="输入要检测的内容，例如：用户在腾讯工作..." />
              </div>
              {duplicates.length > 0 ? (
                <Table dataSource={duplicates} rowKey={(r, i) => r.id || i} size="small"
                  columns={[
                    { title: '#', width: 40, render: (_: any, __: any, i: number) => i + 1 },
                    { title: '匹配内容', dataIndex: 'content', ellipsis: true },
                    { title: '类型', dataIndex: 'fragment_type', width: 80 },
                    { title: '相似度', dataIndex: 'similarity', width: 80, render: (v: number) => (v * 100).toFixed(1) + '%' },
                    { title: '创建时间', dataIndex: 'created_at', width: 130, render: (v: string) => v ? new Date(v).toLocaleString() : '-' },
                    { title: '操作', width: 140, render: (_: any, r: any) => (
                      <Popconfirm title={`合并到选中的内容？`} onConfirm={() => {
                        const allIds = [r.id].concat(duplicates.filter(d => d.id !== r.id).map(d => d.id));
                        handleMerge(allIds, mergeContent || r.content);
                      }}>
                        <Button size="small" icon={<MergeCellsOutlined />}>合并到此</Button>
                      </Popconfirm>
                    )},
                  ]} />
              ) : <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>输入内容后点击"查找重复"开始检测</div>}
            </Card>
          ),
        },

        // ======== Tab 6: Conflict Resolution ========
        {
          key: 'conflicts', label: <span><WarningOutlined /> 冲突解决</span>,
          children: (
            <div>
              <Card title="冲突检测" size="small" style={{ marginBottom: 16 }}
                extra={<Tag color="warning">例如: user_company = "腾讯" → 新值 "阿里"</Tag>}>
                <Space>
                  <Input value={conflictKey} onChange={e => setConflictKey(e.target.value)} placeholder="变量 key，如 user_company" style={{ width: 200 }} />
                  <Input value={conflictValue} onChange={e => setConflictValue(e.target.value)} placeholder="新值，如 阿里" style={{ width: 200 }} />
                  <Button type="primary" icon={<WarningOutlined />} onClick={handleDetectConflicts} loading={detectConflictMutation.isPending}>检测冲突</Button>
                </Space>
              </Card>

              <Card title="待处理冲突"
                size="small"
                style={{ marginBottom: 16 }}
                extra={
                  <Space>
                    <Select
                      placeholder="按类型筛选"
                      allowClear
                      style={{ width: 140 }}
                      value={typeFilter}
                      onChange={setTypeFilter}
                      options={[
                        { value: 'value_mismatch', label: '值不一致' },
                        { value: 'source_conflict', label: '来源冲突' },
                        { value: 'time_conflict', label: '时间冲突' },
                      ]}
                    />
                    <DatePicker.RangePicker
                      value={dateRange}
                      onChange={(vals) => setDateRange(vals as any)}
                      style={{ width: 240 }}
                    />
                    <Button icon={<ReloadOutlined />} onClick={() => refetchConflicts()} loading={conflictsLoading}>刷新</Button>
                  </Space>
                }>
                <Table
                  dataSource={filteredConflicts}
                  rowKey="id"
                  size="small"
                  loading={conflictsLoading}
                  pagination={{ pageSize: 10 }}
                  columns={[
                    { title: '冲突 ID', dataIndex: 'id', width: 80 },
                    { title: '记忆标题/Key', width: 160, render: (_: any, r: any) => <Tag>{getConflictKey(r)}</Tag> },
                    { title: '冲突类型', width: 120, render: (_: any, r: any) => {
                      const t = r.conflict_type || 'value_mismatch';
                      return <Tag color={conflictTypeColor[t] || 'default'}>{conflictTypeMap[t] || t}</Tag>;
                    }},
                    { title: '当前值', dataIndex: 'old_value', ellipsis: true },
                    { title: '冲突值', dataIndex: 'new_value', ellipsis: true },
                    { title: '相似度', dataIndex: 'similarity_score', width: 90, render: (v: number) => v != null ? (v * 100).toFixed(1) + '%' : '-' },
                    { title: '检测时间', dataIndex: 'created_at', width: 150, render: (v: string) => v ? new Date(v).toLocaleString() : '-' },
                    { title: '状态', width: 90, render: () => <Tag color="orange">待处理</Tag> },
                    { title: '操作', width: 90, render: (_: any, r: any) => (
                      <Button size="small" type="primary" onClick={() => openResolveModal(r)}>解决</Button>
                    )},
                  ]}
                />
              </Card>

              <Card title="合并日志 / 解决历史" size="small" extra={<Button icon={<ReloadOutlined />} onClick={() => refetchMergeLog()} loading={mergeLogLoading}>刷新</Button>}>
                {resolvedHistory.length > 0 ? (
                  <Timeline>
                    {resolvedHistory.map((log) => {
                      const resolution = String(log.merge_action || '').replace('resolved:', '') || 'unknown';
                      const resolutionLabel: Record<string, string> = {
                        accept_new: '采用新值',
                        keep_current: '保留当前',
                        manual: '手动合并',
                      };
                      return (
                        <Timeline.Item key={log.id} color="green">
                          <div style={{ fontWeight: 500 }}>{new Date(log.resolved_at || log.created_at).toLocaleString()}</div>
                          <div style={{ color: '#666' }}>
                            操作者：<Tag>{log.operator || 'user'}</Tag>
                            解决方式：<Tag color="blue">{resolutionLabel[resolution] || resolution}</Tag>
                            涉及记忆：<Tag>{getConflictKey(log)}</Tag>
                          </div>
                          <div style={{ marginTop: 4, color: '#333' }}>最终值：{log.new_value || '-'}</div>
                        </Timeline.Item>
                      );
                    })}
                  </Timeline>
                ) : (
                  <div style={{ textAlign: 'center', padding: 24, color: '#999' }}>暂无已解决的冲突记录</div>
                )}
              </Card>
            </div>
          ),
        },
      ]} />

      {/* Resolve Conflict Modal */}
      <Modal
        title={`解决冲突 ${selectedConflict ? '#'+selectedConflict.id : ''}`}
        open={resolveModalOpen}
        onCancel={closeResolveModal}
        footer={null}
        width={720}
      >
        {selectedConflict && (
          <div>
            <Descriptions size="small" column={2} bordered style={{ marginBottom: 16 }}>
              <Descriptions.Item label="冲突 ID">{selectedConflict.id}</Descriptions.Item>
              <Descriptions.Item label="记忆 Key">{getConflictKey(selectedConflict)}</Descriptions.Item>
              <Descriptions.Item label="冲突类型">
                <Tag color={conflictTypeColor[selectedConflict.conflict_type || 'value_mismatch'] || 'default'}>
                  {conflictTypeMap[selectedConflict.conflict_type || 'value_mismatch'] || selectedConflict.conflict_type}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="检测时间">{selectedConflict.created_at ? new Date(selectedConflict.created_at).toLocaleString() : '-'}</Descriptions.Item>
            </Descriptions>

            <Row gutter={16} style={{ marginBottom: 16 }}>
              <Col span={12}>
                <div style={{ fontWeight: 500, marginBottom: 8 }}>当前值（已保存）</div>
                <div style={{
                  background: '#f6ffed',
                  border: '1px solid #b7eb8f',
                  borderRadius: 6,
                  padding: 12,
                  minHeight: 120,
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                }}>
                  {selectedConflict.old_value || '-'}
                </div>
              </Col>
              <Col span={12}>
                <div style={{ fontWeight: 500, marginBottom: 8 }}>冲突值（新值）</div>
                <div style={{
                  background: '#fff2f0',
                  border: '1px solid #ffa39e',
                  borderRadius: 6,
                  padding: 12,
                  minHeight: 120,
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                }}>
                  {selectedConflict.new_value || '-'}
                </div>
              </Col>
            </Row>

            {resolutionMode === 'manual' && (
              <div style={{ marginBottom: 16 }}>
                <div style={{ fontWeight: 500, marginBottom: 8 }}>手动编辑最终值</div>
                <Input.TextArea
                  rows={4}
                  value={manualValue}
                  onChange={e => setManualValue(e.target.value)}
                  placeholder="请输入合并后的最终值..."
                />
              </div>
            )}

            <Space style={{ display: 'flex', justifyContent: 'flex-end' }}>
              <Button onClick={closeResolveModal}>取消</Button>
              <Button onClick={() => { setResolutionMode('keep_current'); setManualValue(selectedConflict.old_value || ''); }} type={resolutionMode === 'keep_current' ? 'primary' : 'default'}>
                保留当前
              </Button>
              <Button onClick={() => { setResolutionMode('accept_new'); setManualValue(selectedConflict.new_value || ''); }} type={resolutionMode === 'accept_new' ? 'primary' : 'default'}>
                采用新值
              </Button>
              <Button onClick={() => { setResolutionMode('manual'); }} type={resolutionMode === 'manual' ? 'primary' : 'default'}>
                手动合并
              </Button>
              <Button type="primary" danger onClick={handleResolve} loading={resolveConflictMutation.isPending} disabled={!resolutionMode}>
                确认解决
              </Button>
            </Space>
          </div>
        )}
      </Modal>
    </div>
  );
}
