import { useState } from 'react';
import { Card, Row, Col, Statistic, Table, Tag, Progress, Radio, Space, Badge, Spin } from 'antd';
import { ClockCircleOutlined, ThunderboltOutlined, DatabaseOutlined, WarningOutlined, ReloadOutlined } from '@ant-design/icons';
import {
  usePerformanceLatency,
  usePerformanceLlmCosts,
  usePerformanceCache,
  usePerformanceErrors,
} from '../hooks/useMemoryQueries';

const TIME_OPTIONS = [
  { value: 1, label: '最近1小时' },
  { value: 6, label: '最近6小时' },
  { value: 24, label: '最近24小时' },
  { value: 168, label: '最近7天' },
];

interface LatencyRow {
  endpoint: string;
  count: number;
  p50: number;
  p95: number;
  p99: number;
  avg: number;
  max: number;
}

interface LlmModelRow {
  model: string;
  calls: number;
  total_tokens: number;
  avg_latency_ms: number;
  total_cost: number;
}

interface CacheRow {
  cache_type: string;
  hits: number;
  misses: number;
  total: number;
  hit_rate: number;
}

interface ErrorItem {
  id: number;
  endpoint: string;
  status_code: number;
  error_message: string;
  created_at: string;
}

export default function PerformanceTab() {
  const [hours, setHours] = useState(24);
  const { data: latencyData, isLoading: latencyLoading } = usePerformanceLatency(hours);
  const { data: llmData, isLoading: llmLoading } = usePerformanceLlmCosts(hours);
  const { data: cacheData, isLoading: cacheLoading } = usePerformanceCache(hours);
  const { data: errorData, isLoading: errorLoading } = usePerformanceErrors(hours);

  const latencyRows: LatencyRow[] = latencyData?.endpoints || [];
  const llmModels: LlmModelRow[] = llmData?.models || [];
  const cacheRows: CacheRow[] = cacheData?.caches || [];
  const errorItems: ErrorItem[] = errorData?.recent_errors || [];

  const maxP99 = Math.max(1, ...(latencyRows.map((r) => r.p99) || [1]));

  return (
    <div>
      <Card size="small" style={{ marginBottom: 16 }}>
        <Space>
          <ClockCircleOutlined />
          <span>时间范围:</span>
          <Radio.Group value={hours} onChange={(e) => setHours(e.target.value)}>
            {TIME_OPTIONS.map((opt) => (
              <Radio.Button key={opt.value} value={opt.value}>
                {opt.label}
              </Radio.Button>
            ))}
          </Radio.Group>
        </Space>
      </Card>

      <Row gutter={[16, 16]}>
        {/* API Latency */}
        <Col span={24}>
          <Card title="API 延迟分布" size="small" loading={latencyLoading}>
            {latencyRows.length === 0 ? (
              <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>暂无数据</div>
            ) : (
              <Table
                dataSource={latencyRows}
                rowKey="endpoint"
                size="small"
                pagination={false}
                columns={[
                  { title: '端点', dataIndex: 'endpoint', ellipsis: true },
                  { title: '请求数', dataIndex: 'count', width: 80 },
                  { title: 'P50(ms)', dataIndex: 'p50', width: 90, render: (v: number) => v.toFixed(1) },
                  { title: 'P95(ms)', dataIndex: 'p95', width: 90, render: (v: number) => v.toFixed(1) },
                  {
                    title: 'P99(ms)',
                    dataIndex: 'p99',
                    width: 120,
                    render: (v: number) => (
                      <span style={{ color: v > 2000 ? '#ff4d4f' : 'inherit', fontWeight: v > 2000 ? 'bold' : 'normal' }}>
                        {v.toFixed(1)}
                        {v > 2000 && <Tag color="error" style={{ marginLeft: 8 }}>超标</Tag>}
                      </span>
                    ),
                  },
                  {
                    title: '分布',
                    dataIndex: 'p99',
                    width: 160,
                    render: (v: number) => (
                      <div style={{ display: 'flex', alignItems: 'center' }}>
                        <div
                          style={{
                            width: `${Math.min(100, (v / maxP99) * 100)}%`,
                            height: 12,
                            background: v > 2000 ? '#ff4d4f' : '#1677ff',
                            borderRadius: 2,
                            marginRight: 8,
                          }}
                        />
                        <span style={{ fontSize: 12, color: '#999' }}>{Math.min(100, Math.round((v / maxP99) * 100))}%</span>
                      </div>
                    ),
                  },
                ]}
              />
            )}
          </Card>
        </Col>

        {/* LLM Stats */}
        <Col span={24}>
          <Card title="LLM 调用统计" size="small" loading={llmLoading}>
            <Row gutter={16} style={{ marginBottom: 16 }}>
              <Col span={8}>
                <Statistic title="总调用次数" value={llmData?.total_calls ?? 0} prefix={<ThunderboltOutlined />} />
              </Col>
              <Col span={8}>
                <Statistic title="总Token数" value={llmData?.total_tokens ?? 0} prefix={<DatabaseOutlined />} />
              </Col>
              <Col span={8}>
                <Statistic title="总成本估算" value={llmData?.total_cost ?? 0} prefix="$" precision={4} />
              </Col>
            </Row>
            <Table
              dataSource={llmModels}
              rowKey="model"
              size="small"
              pagination={false}
              columns={[
                { title: '模型', dataIndex: 'model' },
                { title: '调用次数', dataIndex: 'calls', width: 100 },
                { title: '总Token数', dataIndex: 'total_tokens', width: 120 },
                { title: '平均延迟(ms)', dataIndex: 'avg_latency_ms', width: 130, render: (v: number) => (v || 0).toFixed(1) },
                { title: '总成本($)', dataIndex: 'total_cost', width: 120, render: (v: number) => (v || 0).toFixed(4) },
              ]}
            />
          </Card>
        </Col>

        {/* Cache Hit Rate */}
        <Col span={12}>
          <Card title="缓存命中率" size="small" loading={cacheLoading}>
            <Row gutter={16}>
              <Col span={12} style={{ textAlign: 'center' }}>
                <Progress
                  type="circle"
                  percent={Math.round((cacheData?.overall_hit_rate || 0) * 100)}
                  format={(percent) => `${percent}%`}
                  status={((cacheData?.overall_hit_rate || 0) * 100) > 50 ? 'success' : 'normal'}
                />
                <div style={{ marginTop: 8, color: '#666' }}>总体命中率</div>
              </Col>
              <Col span={12}>
                {cacheRows.length === 0 ? (
                  <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>暂无数据</div>
                ) : (
                  <div>
                    {cacheRows.map((c) => (
                      <div key={c.cache_type} style={{ marginBottom: 16 }}>
                        <div style={{ marginBottom: 4, display: 'flex', justifyContent: 'space-between' }}>
                          <span>{c.cache_type}</span>
                          <span>{Math.round(c.hit_rate * 100)}%</span>
                        </div>
                        <Progress percent={Math.round(c.hit_rate * 100)} size="small" />
                        <div style={{ fontSize: 12, color: '#999' }}>
                          命中 {c.hits} / 未命中 {c.misses}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </Col>
            </Row>
          </Card>
        </Col>

        {/* Error Rate */}
        <Col span={12}>
          <Card
            title={
              <span>
                <WarningOutlined style={{ color: '#ff4d4f', marginRight: 8 }} />
                错误率
              </span>
            }
            size="small"
            loading={errorLoading}
          >
            <Row gutter={16} style={{ marginBottom: 16 }}>
              <Col span={8}>
                <Statistic
                  title="总错误数"
                  value={errorData?.total_errors ?? 0}
                  valueStyle={{ color: (errorData?.total_errors || 0) > 0 ? '#ff4d4f' : 'inherit' }}
                />
              </Col>
              <Col span={8}>
                <Statistic
                  title="错误率"
                  value={Math.round((errorData?.error_rate || 0) * 100)}
                  suffix="%"
                  valueStyle={{ color: ((errorData?.error_rate || 0) * 100) > 5 ? '#ff4d4f' : 'inherit' }}
                />
              </Col>
              <Col span={8}>
                <Statistic
                  title="重试成功率"
                  value={Math.round((errorData?.retry_success_rate || 0) * 100)}
                  suffix="%"
                  prefix={<ReloadOutlined />}
                />
              </Col>
            </Row>
            {errorItems.length === 0 ? (
              <div style={{ textAlign: 'center', padding: 20, color: '#999' }}>最近无错误</div>
            ) : (
              <Table
                dataSource={errorItems}
                rowKey="id"
                size="small"
                pagination={false}
                scroll={{ y: 240 }}
                columns={[
                  { title: '时间', dataIndex: 'created_at', width: 150, render: (v: string) => (v ? new Date(v).toLocaleString() : '-') },
                  { title: '端点', dataIndex: 'endpoint', ellipsis: true },
                  { title: '状态码', dataIndex: 'status_code', width: 80, render: (v: number) => <Badge status={v >= 500 ? 'error' : 'warning'} text={v || '-'} /> },
                  { title: '错误信息', dataIndex: 'error_message', ellipsis: true },
                ]}
              />
            )}
          </Card>
        </Col>
      </Row>
    </div>
  );
}
