import { useState } from 'react';
import { Card, Input, Button, Select, Slider, Tag, Descriptions, Empty, Space, message, Spin, Alert } from 'antd';
import { ThunderboltOutlined } from '@ant-design/icons';
import { playgroundApi } from '../../services/api';

const FRAGMENT_TYPES = [
  { value: 'info', label: 'info（事实信息，永久）' },
  { value: 'plan', label: 'plan（计划安排，90天）' },
  { value: 'preference', label: 'preference（临时偏好，1天）' },
];

const ACTION_COLOR: Record<string, string> = {
  superseded_old: 'red',
  kept_old: 'green',
  pending_review: 'orange',
};

export default function InjectionSimulator() {
  const [content, setContent] = useState('');
  const [fragmentType, setFragmentType] = useState('info');
  const [importance, setImportance] = useState(0.5);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<any>(null);

  const handleSimulate = async () => {
    if (!content.trim()) { message.warning('请输入模拟内容'); return; }
    setLoading(true);
    try {
      const res = await playgroundApi.simulateInjection({ content, fragment_type: fragmentType, importance });
      setResult(res.data);
    } catch { message.error('注入模拟失败'); }
    setLoading(false);
  };

  return (
    <Spin spinning={loading}>
      <Card className="section-card" title="模拟输入">
        <Space orientation="vertical" style={{ width: '100%' }}>
          <Input.TextArea
            rows={3}
            value={content}
            onChange={e => setContent(e.target.value)}
            placeholder="输入一条记忆内容，如：我搬到杭州了 / 我下周三要去上海出差"
          />
          <Space wrap>
            <span>类型:</span>
            <Select value={fragmentType} onChange={setFragmentType} options={FRAGMENT_TYPES} style={{ width: 240 }} />
            <span>重要性:</span>
            <Slider min={0} max={1} step={0.05} value={importance} onChange={setImportance} style={{ width: 160 }} />
            <Tag>{importance.toFixed(2)}</Tag>
            <Button type="primary" icon={<ThunderboltOutlined />} onClick={handleSimulate}>模拟注入</Button>
          </Space>
        </Space>
      </Card>

      {result && (
        <>
          <Card className="section-card" title="① 实体抽取" size="small">
            {result.entity ? (
              <Space>
                <Tag color="blue">{result.entity.entity_type}</Tag>
                <span>{result.entity.entity_value}</span>
              </Space>
            ) : <Empty description="未识别到可更新实体" image={Empty.PRESENTED_IMAGE_SIMPLE} />}
          </Card>

          <Card className="section-card" title="② 矛盾检测（只读预判）" size="small">
            {result.contradictions?.length > 0 ? result.contradictions.map((c: any, i: number) => (
              <Card key={i} size="small" style={{ marginBottom: 8 }}>
                <Descriptions size="small" column={2}>
                  <Descriptions.Item label="旧内容" span={2}>{c.old_content}</Descriptions.Item>
                  <Descriptions.Item label="变化">{c.old_value} → {c.new_value}</Descriptions.Item>
                  <Descriptions.Item label="相似度">{Number(c.similarity_score).toFixed(2)}</Descriptions.Item>
                  <Descriptions.Item label="预测策略">{c.predicted_strategy}</Descriptions.Item>
                  <Descriptions.Item label="预测动作">
                    <Tag color={ACTION_COLOR[c.predicted_action] || 'default'}>{c.predicted_action}</Tag>
                  </Descriptions.Item>
                </Descriptions>
              </Card>
            )) : <Empty description="未检测到矛盾" image={Empty.PRESENTED_IMAGE_SIMPLE} />}
          </Card>

          <Card className="section-card" title="③ 时间推断" size="small">
            <Descriptions size="small" column={3}>
              <Descriptions.Item label="生效时间">{result.temporal?.valid_from || '-'}</Descriptions.Item>
              <Descriptions.Item label="失效时间">{result.temporal?.valid_until || '永久有效'}</Descriptions.Item>
              <Descriptions.Item label="有失效期">
                <Tag color={result.temporal?.has_expiry ? 'orange' : 'green'}>
                  {result.temporal?.has_expiry ? '是' : '否'}
                </Tag>
              </Descriptions.Item>
            </Descriptions>
          </Card>

          <Card className="section-card" title="④ 最终片段预览" size="small">
            {result.fragment_preview?.would_supersede?.length > 0 && (
              <Alert
                type="warning"
                showIcon
                style={{ marginBottom: 8 }}
                message={`真实注入将取代 ${result.fragment_preview.would_supersede.length} 条旧记忆（ID: ${result.fragment_preview.would_supersede.join(', ')}）`}
              />
            )}
            <pre className="code-block">{JSON.stringify(result.fragment_preview, null, 2)}</pre>
          </Card>
        </>
      )}
    </Spin>
  );
}
