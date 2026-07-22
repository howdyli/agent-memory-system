import { useState, useEffect, useMemo } from 'react';
import { Card, Table, Button, Modal, Form, Input, Select, Space, Popconfirm, message, Tag, Row, Col, Upload, Alert } from 'antd';
import { PlusOutlined, DeleteOutlined, ReloadOutlined, SearchOutlined, ImportOutlined, InboxOutlined } from '@ant-design/icons';
import type { UploadProps } from 'antd';
import { tablesApi } from '../services/api';
import { useTables, useTableRecords, useCreateTable, useDropTable, useAddRecord, useDeleteRecord, useBatchDeleteRecords, useBatchImportRecords } from '../hooks/useMemoryQueries';
import AdvancedFilter, { filterRecords, type FilterCondition, type FilterField, type FilterLogic } from '../components/AdvancedFilter';
import BatchOperationBar from '../components/BatchOperationBar';
import type { BatchAction } from '../components/BatchOperationBar';

interface MemTable { table_name: string; description?: string; row_count?: number; fields?: { name: string; type: string }[] }
interface RecordRow { id: number; [key: string]: unknown }

const FIELD_TYPES = ['TEXT', 'INTEGER', 'REAL', 'BOOLEAN', 'DATE', 'DATETIME', 'JSON'];

export default function TablesPage() {
  const { data: tables = [], isLoading, refetch: refetchTables } = useTables();
  const [selectedTable, setSelectedTable] = useState<string | null>(null);
  const { data: records = [], isLoading: recordsLoading } = useTableRecords(selectedTable || '');
  const createTable = useCreateTable();
  const dropTable = useDropTable();
  const addRecord = useAddRecord(selectedTable || '');
  const deleteRecord = useDeleteRecord(selectedTable || '');

  const [createModal, setCreateModal] = useState(false);
  const [addModal, setAddModal] = useState(false);
  const [nlModal, setNlModal] = useState(false);
  const [nlQuery, setNlQuery] = useState('');
  const [nlResult, setNlResult] = useState<any>(null);
  const [nlStep, setNlStep] = useState<'input' | 'preview'>('input');
  const [nlPreview, setNlPreview] = useState<{ sql: string; is_safe: boolean; safety_reason: string } | null>(null);
  const [editableSql, setEditableSql] = useState('');
  const [nlLoading, setNlLoading] = useState(false);
  const [createForm] = Form.useForm();
  const [addForm] = Form.useForm();

  const [filterConditions, setFilterConditions] = useState<FilterCondition[]>([]);
  const [filterLogic, setFilterLogic] = useState<FilterLogic>('AND');

  // 切换表时清空选中
  useEffect(() => { setSelectedRecordIds([]); }, [selectedTable]);

  // 批量操作状态
  const [selectedRecordIds, setSelectedRecordIds] = useState<number[]>([]);
  const batchDeleteRecords = useBatchDeleteRecords(selectedTable || '');
  const batchImportRecords = useBatchImportRecords(selectedTable || '');

  // 导入状态
  const [importModal, setImportModal] = useState(false);
  const [importData, setImportData] = useState<Record<string, unknown>[]>([]);
  const [importFileName, setImportFileName] = useState('');
  const [importLoading, setImportLoading] = useState(false);

  const handleCreateTable = async () => {
    const vals = await createForm.validateFields();
    try {
      await createTable.mutateAsync({ table_name: vals.table_name, fields: vals.fields || [] });
      message.success('表创建成功');
      setCreateModal(false);
      createForm.resetFields();
    } catch (e: unknown) { message.error((e as Error).message); }
  };

  const handleDropTable = async (name: string) => {
    // 先清空选中，禁用该表的记录查询，避免删除后失效刷新触发对已删表的请求（400）
    if (selectedTable === name) setSelectedTable(null);
    try {
      await dropTable.mutateAsync(name);
      message.success('表已删除');
    } catch { message.error('删除失败'); }
  };

  const handleAddRecord = async () => {
    if (!selectedTable) return;
    const vals = await addForm.validateFields();
    try {
      await addRecord.mutateAsync(vals);
      message.success('记录已添加');
      setAddModal(false);
      addForm.resetFields();
    } catch (e: unknown) { message.error((e as Error).message); }
  };

  const handleDeleteRecord = async (id: number) => {
    if (!selectedTable) return;
    try { await deleteRecord.mutateAsync(id); message.success('已删除'); }
    catch { message.error('删除失败'); }
  };

  const handleBatchDeleteRecords = () => {
    if (!selectedTable || selectedRecordIds.length === 0) return;
    Modal.confirm({
      title: '确认批量删除',
      content: `将删除 ${selectedRecordIds.length} 条记录，此操作不可撤销。`,
      okText: '删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: async () => {
        try {
          await batchDeleteRecords.mutateAsync(selectedRecordIds);
          message.success(`已删除 ${selectedRecordIds.length} 条记录`);
          setSelectedRecordIds([]);
        } catch { message.error('批量删除失败'); }
      },
    });
  };

  // ---- CSV / JSON 导入逻辑 ----

  const parseCSV = (text: string): Record<string, unknown>[] => {
    const lines = text.split(/\r?\n/).filter((l) => l.trim());
    if (lines.length < 2) return []; // 至少需要表头 + 1 行数据
    const headers = lines[0].split(',').map((h) => h.trim());
    return lines.slice(1).map((line) => {
      const values = line.split(',').map((v) => v.trim());
      const row: Record<string, unknown> = {};
      headers.forEach((h, i) => { row[h] = values[i] ?? ''; });
      return row;
    });
  };

  const handleImportFile: UploadProps['beforeUpload'] = (file) => {
    const isCSV = file.name.endsWith('.csv');
    const isJSON = file.name.endsWith('.json');
    if (!isCSV && !isJSON) {
      message.error('仅支持 .csv 和 .json 文件');
      return Upload.LIST_IGNORE;
    }
    if (file.size > 5 * 1024 * 1024) {
      message.error('文件大小不能超过 5MB');
      return Upload.LIST_IGNORE;
    }
    const reader = new FileReader();
    reader.onload = (e) => {
      const content = e.target?.result as string;
      try {
        let data: Record<string, unknown>[] = [];
        if (isJSON) {
          const parsed = JSON.parse(content);
          data = Array.isArray(parsed) ? parsed : [parsed];
        } else {
          data = parseCSV(content);
        }
        if (data.length === 0) {
          message.error('文件中没有有效数据');
          return;
        }
        setImportData(data);
        setImportFileName(file.name);
        message.success(`已解析 ${data.length} 条记录`);
      } catch {
        message.error('文件解析失败，请检查格式');
      }
    };
    reader.readAsText(file);
    return false; // 阻止自动上传
  };

  const handleConfirmImport = async () => {
    if (!selectedTable || importData.length === 0) return;
    setImportLoading(true);
    try {
      await batchImportRecords.mutateAsync(importData);
      message.success(`成功导入 ${importData.length} 条记录`);
      setImportModal(false);
      setImportData([]);
      setImportFileName('');
    } catch { message.error('导入失败'); }
    finally { setImportLoading(false); }
  };

  const closeImportModal = () => {
    setImportModal(false);
    setImportData([]);
    setImportFileName('');
  };

  const openNlModal = () => {
    setNlModal(true);
    setNlStep('input');
    setNlResult(null);
    setNlPreview(null);
    setEditableSql('');
  };

  const closeNlModal = () => {
    setNlModal(false);
    setNlStep('input');
    setNlResult(null);
    setNlPreview(null);
    setEditableSql('');
  };

  const handleGenerateSql = async () => {
    if (!nlQuery.trim() || !selectedTable) return;
    setNlLoading(true);
    try {
      const res = await tablesApi.nlToSql(selectedTable, nlQuery);
      const data = res.data;
      if (!data.success) {
        message.error(data.error || '生成 SQL 失败');
        return;
      }
      setNlPreview(data);
      setEditableSql(data.sql || '');
      setNlStep('preview');
    } catch (e: unknown) {
      message.error((e as Error).message || '生成 SQL 失败');
    } finally {
      setNlLoading(false);
    }
  };

  const handleExecuteSql = async () => {
    if (!selectedTable || !editableSql.trim()) return;
    setNlLoading(true);
    try {
      const res = await tablesApi.executeSql(selectedTable, editableSql);
      setNlResult(res.data);
      setNlStep('input');
      setNlPreview(null);
      message.success('查询完成');
    } catch (e: unknown) {
      message.error((e as Error).message || '执行失败');
    } finally {
      setNlLoading(false);
    }
  };

  const handleBackToInput = () => {
    setNlStep('input');
    setNlPreview(null);
    setEditableSql('');
  };

  const selectedFields = tables.find((t: MemTable) => t.table_name === selectedTable)?.fields || [];

  const filteredTables = useMemo(() => {
    return filterRecords(tables as Record<string, unknown>[], filterConditions, filterLogic) as unknown as MemTable[];
  }, [tables, filterConditions, filterLogic]);

  const tableFilterFields: FilterField[] = [
    { key: 'table_name', label: '表名', type: 'text', placeholder: '表名关键词' },
    { key: 'created_at', label: '创建时间', type: 'dateRange' },
    { key: 'row_count', label: '记录数', type: 'number' },
  ];

  return (
    <div>
      <div className="page-header">
        <h2>记忆表</h2>
        <p>动态结构化记忆存储，支持自定义 Schema、过滤查询和自然语言查询</p>
      </div>

      <AdvancedFilter
        fields={tableFilterFields}
        pageName="tables"
        onFilter={(conditions, logic) => {
          setFilterConditions(conditions);
          setFilterLogic(logic);
        }}
        onReset={() => {
          setFilterConditions([]);
          setFilterLogic('AND');
        }}
      />
      <Row gutter={16}>
        <Col span={8}>
          <Card title="记忆表列表" extra={<Space><Button size="small" icon={<ReloadOutlined />} onClick={() => refetchTables()} /><Button size="small" type="primary" icon={<PlusOutlined />} onClick={() => { createForm.resetFields(); setCreateModal(true); }}>新建</Button></Space>} className="section-card">
            <Table<MemTable>
              dataSource={filteredTables}
              rowKey="table_name"
              loading={isLoading}
              size="small"
              pagination={false}
              onRow={(r) => ({ onClick: () => setSelectedTable(r.table_name), style: { cursor: 'pointer', background: selectedTable === r.table_name ? '#e6f4ff' : undefined } })}
              columns={[
                { title: '表名', dataIndex: 'table_name', ellipsis: true },
                { title: '行数', dataIndex: 'row_count', width: 60 },
                { title: '', width: 40, render: (_, r) => (
                  <Popconfirm title="确认删除此表？" onConfirm={() => handleDropTable(r.table_name)}>
                    <Button size="small" danger icon={<DeleteOutlined />} />
                  </Popconfirm>
                )},
              ]}
              locale={{ emptyText: '暂无记忆表' }}
            />
          </Card>
        </Col>
        <Col span={16}>
          <Card
            title={selectedTable ? `表: ${selectedTable}` : '请选择记忆表'}
            className="section-card"
            extra={selectedTable && <Space>
              <Button icon={<SearchOutlined />} onClick={openNlModal}>自然语言查询</Button>
              <Button icon={<ImportOutlined />} onClick={() => { setImportData([]); setImportFileName(''); setImportModal(true); }}>导入</Button>
              <Button type="primary" icon={<PlusOutlined />} onClick={() => { addForm.resetFields(); setAddModal(true); }}>添加记录</Button>
            </Space>}
          >
            {selectedTable ? (
              <>
                {selectedFields.length > 0 && (
                  <div style={{ marginBottom: 12 }}>{selectedFields.map((f: { name: string; type: string }) => <Tag key={f.name}>{f.name}: {f.type}</Tag>)}</div>
                )}
                <Table
                  dataSource={records as RecordRow[]}
                  rowKey="id"
                  loading={recordsLoading}
                  size="small"
                  scroll={{ x: true }}
                  rowSelection={{
                    selectedRowKeys: selectedRecordIds,
                    onChange: (keys) => setSelectedRecordIds(keys as number[]),
                  }}
                  columns={[
                    { title: 'ID', dataIndex: 'id', width: 60 },
                    ...selectedFields.map((f: { name: string; type: string }) => ({ title: f.name, dataIndex: f.name, ellipsis: true, render: (v: unknown) => typeof v === 'object' ? JSON.stringify(v) : String(v ?? '-') })),
                    { title: '操作', width: 60, render: (_, r) => (
                      <Popconfirm title="删除？" onConfirm={() => handleDeleteRecord(r.id)}>
                        <Button size="small" danger icon={<DeleteOutlined />} />
                      </Popconfirm>
                    )},
                  ]}
                  locale={{ emptyText: '暂无记录' }}
                />
              </>
            ) : <div style={{ textAlign: 'center', color: '#999', padding: 40 }}>← 请从左侧选择一张记忆表</div>}
          </Card>
        </Col>
      </Row>

      {/* Create Table Modal */}
      <Modal title="新建记忆表" open={createModal} onOk={handleCreateTable} onCancel={() => setCreateModal(false)} width={560}>
        <Form form={createForm} layout="vertical">
          <Form.Item name="table_name" label="表名" rules={[{ required: true }]}><Input placeholder="my_memory_table" /></Form.Item>
          <Form.Item name="description" label="描述"><Input.TextArea rows={2} /></Form.Item>
          <Form.List name="fields">
            {(fields, { add, remove }) => (
              <>
                {fields.map(({ key, name, ...rest }) => (
                  <Space key={key} style={{ display: 'flex', marginBottom: 8 }} align="baseline">
                    <Form.Item {...rest} name={[name, 'name']} rules={[{ required: true }]}><Input placeholder="字段名" /></Form.Item>
                    <Form.Item {...rest} name={[name, 'type']} rules={[{ required: true }]}>
                      <Select placeholder="类型" style={{ width: 110 }} options={FIELD_TYPES.map(t => ({ value: t, label: t }))} />
                    </Form.Item>
                    <Button danger onClick={() => remove(name)}>×</Button>
                  </Space>
                ))}
                <Button type="dashed" onClick={() => add()} block>+ 添加字段</Button>
              </>
            )}
          </Form.List>
        </Form>
      </Modal>

      {/* Add Record Modal */}
      <Modal title="添加记录" open={addModal} onOk={handleAddRecord} onCancel={() => setAddModal(false)}>
        <Form form={addForm} layout="vertical">
          {selectedFields.map((f: { name: string; type: string }) => (
            <Form.Item key={f.name} name={f.name} label={`${f.name} (${f.type})`}>
              {f.type === 'JSON' || f.type === 'TEXT' ? <Input.TextArea rows={2} /> : <Input />}
            </Form.Item>
          ))}
        </Form>
      </Modal>

      {/* NL Query Modal */}
      <Modal
        title={nlStep === 'preview' ? 'SQL 预览确认' : '自然语言查询'}
        open={nlModal}
        onCancel={closeNlModal}
        width={720}
        footer={
          nlStep === 'input'
            ? [
                <Button key="cancel" onClick={closeNlModal}>取消</Button>,
                <Button key="generate" type="primary" loading={nlLoading} onClick={handleGenerateSql} disabled={!nlQuery.trim()}>
                  生成 SQL
                </Button>,
              ]
            : [
                <Button key="cancel" onClick={closeNlModal}>取消</Button>,
                <Button key="back" onClick={handleBackToInput}>返回编辑</Button>,
                <Button key="execute" type="primary" loading={nlLoading} onClick={handleExecuteSql}>
                  执行
                </Button>,
              ]
        }
      >
        {nlStep === 'input' ? (
          <>
            <Input.TextArea
              rows={3}
              value={nlQuery}
              onChange={e => setNlQuery(e.target.value)}
              placeholder="例如：查找所有已完成的待办事项、统计各类任务数量..."
            />
            {nlResult && (
              <div style={{ marginTop: 16 }}>
                <div style={{ marginBottom: 8, fontWeight: 500 }}>上次查询结果：</div>
                <pre className="code-block">{JSON.stringify(nlResult, null, 2)}</pre>
              </div>
            )}
          </>
        ) : (
          <>
            <div style={{ marginBottom: 8 }}>
              <span style={{ fontWeight: 500 }}>生成的问题：</span>
              <span style={{ color: '#666' }}>{nlQuery}</span>
            </div>
            <div style={{ marginBottom: 8, display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontWeight: 500 }}>SQL 预览：</span>
              {nlPreview && (
                <Tag color={nlPreview.is_safe ? 'success' : 'error'}>
                  {nlPreview.is_safe ? '安全' : '不安全'}
                </Tag>
              )}
            </div>
            <Input.TextArea
              rows={4}
              value={editableSql}
              onChange={e => setEditableSql(e.target.value)}
              className="code-block"
              style={{ fontFamily: 'monospace' }}
            />
            {nlPreview && !nlPreview.is_safe && (
              <div style={{ marginTop: 8, color: '#cf1322' }}>
                安全提示：{nlPreview.safety_reason}
              </div>
            )}
            <div style={{ marginTop: 12, color: '#666', fontSize: 12 }}>
              提示：您可以直接编辑上方 SQL，确认无误后点击“执行”。
            </div>
          </>
        )}
      </Modal>

      {/* Import Modal */}
      <Modal
        title="批量导入记录"
        open={importModal}
        onCancel={closeImportModal}
        width={720}
        footer={importData.length > 0 ? [
          <Button key="cancel" onClick={closeImportModal}>取消</Button>,
          <Button key="import" type="primary" loading={importLoading} onClick={handleConfirmImport}>
            确认导入 ({importData.length} 条)
          </Button>,
        ] : null}
      >
        {!importData.length ? (
          <>
            <Alert
              message="支持 CSV 和 JSON 格式，文件大小限制 5MB"
              type="info"
              style={{ marginBottom: 16 }}
            />
            <Upload.Dragger
              beforeUpload={handleImportFile}
              accept=".csv,.json"
              maxCount={1}
              fileList={[]}
            >
              <p className="ant-upload-drag-icon"><InboxOutlined /></p>
              <p className="ant-upload-text">点击或拖拽文件到此区域上传</p>
              <p className="ant-upload-hint">CSV 第一行为表头，后续行为数据；JSON 为对象数组</p>
            </Upload.Dragger>
          </>
        ) : (
          <>
            <Alert
              message={`已解析 ${importData.length} 条记录`}
              description={`来源文件：${importFileName}`}
              type="success"
              style={{ marginBottom: 16 }}
            />
            <Table
              dataSource={importData.slice(0, 5)}
              rowKey={(_, idx) => String(idx)}
              size="small"
              pagination={false}
              scroll={{ x: true }}
              columns={Object.keys(importData[0] || {}).map((key) => ({
                title: key,
                dataIndex: key,
                ellipsis: true,
                render: (v: unknown) => typeof v === 'object' ? JSON.stringify(v) : String(v ?? '-'),
              }))}
            />
            {importData.length > 5 && (
              <div style={{ marginTop: 8, color: '#999', fontSize: 12 }}>
                仅显示前 5 条预览，共 {importData.length} 条
              </div>
            )}
          </>
        )}
      </Modal>

      {/* Batch Operation Bar */}
      {selectedTable && (
        <BatchOperationBar
          selectedCount={selectedRecordIds.length}
          onClear={() => setSelectedRecordIds([])}
          actions={[
            {
              label: '批量删除',
              icon: <DeleteOutlined />,
              onClick: handleBatchDeleteRecords,
              danger: true,
              loading: batchDeleteRecords.isPending,
            } as BatchAction,
          ]}
        />
      )}
    </div>
  );
}
