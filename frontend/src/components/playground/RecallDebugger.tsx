import { useState } from 'react';
import { Card, Input, Button, Slider, Tag, Progress, Empty, Space, message, Spin, Descriptions } from 'antd';
import { SearchOutlined } from '@ant-design/icons';
import { playgroundApi } from '../../services/api';

function MemoryList({ memories }: { memories: any[] }) {
  if (!memories?.length) return <Empty description="无召回结果" image={Empty.PRESENTED_IMAGE_SIMPLE} />;
  return (
    <>
      {memories.map((m: any, i: number) => (
        <Card key={i} size="small" style={{ marginBottom: 8 }}>
          <Descriptions size="small" column={3}>
            <Descriptions.Item label="ID">{m.id ?? '-'}</Descriptions.Item>
            <Descriptions.Item label="Score">{m.score != null ? Number(m.score).toFixed(4) : '-'}</Descriptions.Item>
            <Descriptions.Item label="状态"><Tag>{m.lifecycle_status}</Tag></Descriptions.Item>
            <Descriptions.Item label="内容" span={3}>{m.content}</Descriptions.Item>
            {m.source_entity && <Descriptions.Item label="来源实体" span={3}><Tag color="purple">{m.source_entity}</Tag></Descriptions.Item>}
          </Descriptions>
        </Card>
      ))}
    </>
  );
}

export default function RecallDebugger() {
  const [query, setQuery] = useState('');
  const [budget, setBudget] = useState(2000);
  const [topK, setTopK] = useState(10);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<any>(null);

  const handleSimulate = async () => {
    if (!query.trim()) { message.warning('请输入查询'); return; }
    setLoading(true);
    try {
      const res = await playgroundApi.simulateRecall({ query, top_k: topK, budget_tokens: budget });
      setResult(res.data);
    } catch { message.error('召回模拟失败'); }
    setLoading(false);
  };

  const utilization = result ? Math.round((result.budget?.utilization ?? 0) * 100) : 0;

  return (
    <Spin spinning={loading}>
      <Card className="section-card" title="召回调试">
        <Space orientation="vertical" style={{ width: '100%' }}>
          <Space.Compact style={{ width: '100%' }}>
            <Input value={query} onChange={e => setQuery(e.target.value)} placeholder="输入查询，观察三层召回的候选与预算分配..." onPressEnter={handleSimulate} />
            <Button type="primary" icon={<SearchOutlined />} onClick={handleSimulate}>模拟召回</Button>
          </Space.Compact>
          <Space wrap>
            <span>Token 预算:</span>
            <Slider min={200} max={8000} step={100} value={budget} onChange={setBudget} style={{ width: 200 }} />
            <Tag>{budget}</Tag>
            <span>Top-K:</span>
            <Slider min={1} max={30} value={topK} onChange={setTopK} style={{ width: 160 }} />
            <Tag>{topK}</Tag>
          </Space>
        </Space>
      </Card>

      {result && (
        <>
          <Card className="section-card" title="Token 预算占用" size="small">
            <Progress
              percent={utilization}
              status={utilization > 95 ? 'exception' : 'active'}
              format={() => `${result.budget?.token_used ?? 0} / ${result.budget?.budget_tokens ?? 0} tokens (${utilization}%)`}
            />
          </Card>

          <Card className="section-card" size="small"
            title={<span>L1 · Profile 变量 <Tag color="blue">{result.level1?.count ?? 0} 条</Tag></span>}>
            {result.level1?.count > 0 ? (
              <Descriptions size="small" column={2} bordered>
                {Object.entries(result.level1.variables || {}).map(([k, v]) => (
                  <Descriptions.Item key={k} label={k}>{String(v)}</Descriptions.Item>
                ))}
              </Descriptions>
            ) : <Empty description="无 KV 变量" image={Empty.PRESENTED_IMAGE_SIMPLE} />}
          </Card>

          <Card className="section-card" size="small"
            title={<span>L2 · 语义召回 <Tag color="green">{result.level2?.memories?.length ?? 0} / 候选 {result.level2?.total_candidates ?? 0}</Tag></span>}>
            <MemoryList memories={result.level2?.memories} />
          </Card>

          <Card className="section-card" size="small"
            title={
              <span>
                L3 · 实体图谱扩展 <Tag color="purple">{result.level3?.memories?.length ?? 0} 条</Tag>
                {result.level3?.excluded_ids?.length > 0 && (
                  <Tag color="orange">跨层去重排除 {result.level3.excluded_ids.length} 条</Tag>
                )}
              </span>
            }>
            <MemoryList memories={result.level3?.memories} />
          </Card>
        </>
      )}
    </Spin>
  );
}
