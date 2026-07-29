import { useState } from 'react';
import { Card, Button, Select, Slider, Tag, Space, message, Spin, Statistic, Row, Col } from 'antd';
import { ExperimentOutlined } from '@ant-design/icons';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip as RTooltip, Legend, ResponsiveContainer } from 'recharts';
import { playgroundApi } from '../../services/api';

const FRAGMENT_TYPES = [
  { value: 'info', label: 'info（永久不衰减）' },
  { value: 'plan', label: 'plan（半衰期 90 天）' },
  { value: 'preference', label: 'preference（半衰期 1 天）' },
  { value: 'other', label: 'other（默认 30 天）' },
];

export default function LifecycleSimulator() {
  const [fragmentType, setFragmentType] = useState('plan');
  const [importance, setImportance] = useState(0.5);
  const [days, setDays] = useState(180);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<any>(null);

  const handleSimulate = async () => {
    setLoading(true);
    try {
      const res = await playgroundApi.simulateDecay({ fragment_type: fragmentType, importance, days });
      setResult(res.data);
    } catch { message.error('衰减模拟失败'); }
    setLoading(false);
  };

  return (
    <Spin spinning={loading}>
      <Card className="section-card" title="生命周期模拟">
        <Space wrap>
          <span>类型:</span>
          <Select value={fragmentType} onChange={setFragmentType} options={FRAGMENT_TYPES} style={{ width: 220 }} />
          <span>重要性:</span>
          <Slider min={0} max={1} step={0.05} value={importance} onChange={setImportance} style={{ width: 160 }} />
          <Tag>{importance.toFixed(2)}</Tag>
          <span>时间快进（天）:</span>
          <Slider min={7} max={730} value={days} onChange={setDays} style={{ width: 200 }} />
          <Tag>{days} 天</Tag>
          <Button type="primary" icon={<ExperimentOutlined />} onClick={handleSimulate}>模拟衰减</Button>
        </Space>
      </Card>

      {result && (
        <>
          <Card className="section-card" size="small">
            <Row gutter={16}>
              <Col span={6}>
                <Statistic title="半衰期（天）" value={result.is_permanent ? '永久' : result.half_life_days} />
              </Col>
              <Col span={6}>
                <Statistic title="有效寿命（5% 阈值）" value={result.is_permanent ? '∞' : `≈ ${result.effective_life_days} 天`} />
              </Col>
              <Col span={6}>
                <Statistic title="模拟时长" value={`${days} 天`} />
              </Col>
              <Col span={6}>
                <Statistic
                  title={`第 ${days} 天衰减分数`}
                  value={result.curve?.length ? result.curve[result.curve.length - 1].decay_score : '-'}
                />
              </Col>
            </Row>
          </Card>

          <Card className="section-card" title="衰减曲线（decay = 2^(-days/half_life)）" size="small">
            <ResponsiveContainer width="100%" height={320}>
              <LineChart data={result.curve || []}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="day" label={{ value: '天数', position: 'insideBottomRight', offset: -4 }} />
                <YAxis domain={[0, 1]} />
                <RTooltip />
                <Legend />
                <Line type="monotone" dataKey="decay_score" name="衰减分数" stroke="#667eea" dot={false} strokeWidth={2} />
                <Line type="monotone" dataKey="effective_score" name="有效分数 (×重要性)" stroke="#f5a623" dot={false} strokeWidth={2} />
              </LineChart>
            </ResponsiveContainer>
          </Card>
        </>
      )}
    </Spin>
  );
}
