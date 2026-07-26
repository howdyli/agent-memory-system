import { useState } from 'react';
import { Card, Button, Select, Input, Tag, Space, message, Spin, Timeline, Empty, Descriptions } from 'antd';
import { HistoryOutlined } from '@ant-design/icons';
import { playgroundApi } from '../../services/api';

const ENTITY_TYPES = [
  { value: 'location', label: 'location（居住地/位置）' },
  { value: 'organization', label: 'organization（公司/组织）' },
  { value: 'title', label: 'title（职位/头衔）' },
  { value: 'status', label: 'status（状态）' },
  { value: 'semantic', label: 'semantic（语义冲突）' },
];

const METHOD_COLOR: Record<string, string> = {
  pattern: 'blue',
  semantic: 'purple',
};

export default function EvolutionTimeline() {
  const [entityType, setEntityType] = useState('location');
  const [entityKey, setEntityKey] = useState('');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<any>(null);

  const handleLoad = async () => {
    setLoading(true);
    try {
      const res = await playgroundApi.evolutionChain(entityType, entityKey.trim() || undefined);
      setResult(res.data);
    } catch { message.error('演变链加载失败'); }
    setLoading(false);
  };

  const chain = result?.chain || [];

  return (
    <Spin spinning={loading}>
      <Card className="section-card" title="冲突演变链追溯">
        <Space wrap>
          <span>实体类型:</span>
          <Select value={entityType} onChange={setEntityType} options={ENTITY_TYPES} style={{ width: 240 }} />
          <Input
            value={entityKey}
            onChange={e => setEntityKey(e.target.value)}
            placeholder="实体 key（可选）"
            style={{ width: 180 }}
          />
          <Button type="primary" icon={<HistoryOutlined />} onClick={handleLoad}>加载演变链</Button>
          {result && <Tag color="blue">共 {result.total_versions ?? chain.length} 个版本</Tag>}
        </Space>
      </Card>

      {result && (
        <Card className="section-card" title="演变时间线（v1 → v2 → v3 ...）" size="small">
          {chain.length > 0 ? (
            <Timeline
              items={chain.map((e: any, i: number) => ({
                color: i === chain.length - 1 ? 'green' : 'gray',
                content: (
                  <div>
                    <Space wrap style={{ marginBottom: 4 }}>
                      <Tag color="geekblue">v{i + 1}</Tag>
                      <Tag color={METHOD_COLOR[e.detection_method] || 'default'}>{e.detection_method}</Tag>
                      <span style={{ color: '#999' }}>{e.observed_at}</span>
                    </Space>
                    <Descriptions size="small" column={2}>
                      <Descriptions.Item label="旧值">{e.old_value || '-'}</Descriptions.Item>
                      <Descriptions.Item label="新值">{e.new_value || '-'}</Descriptions.Item>
                      {e.similarity_score != null && (
                        <Descriptions.Item label="相似度">{Number(e.similarity_score).toFixed(2)}</Descriptions.Item>
                      )}
                      {e.change_reason && (
                        <Descriptions.Item label="变化原因">{e.change_reason}</Descriptions.Item>
                      )}
                    </Descriptions>
                  </div>
                ),
              }))}
            />
          ) : <Empty description="该实体类型暂无演变记录，可先在「注入模拟器」中体验矛盾检测" />}
        </Card>
      )}
    </Spin>
  );
}
