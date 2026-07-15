import { useState } from 'react';
import { Card, Input, Button, Space, Tabs, Tag, Descriptions, message, Spin, Empty } from 'antd';
import { ThunderboltOutlined, SendOutlined } from '@ant-design/icons';
import { extractionApi } from '../services/api';

export default function ExtractionPage() {
  const [text, setText] = useState('');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [conversations, setConversations] = useState([{ role: 'user', content: '' }, { role: 'assistant', content: '' }]);
  const [batchResult, setBatchResult] = useState<Record<string, unknown> | null>(null);
  const [summary, setSummary] = useState<string>('');
  const [context, setContext] = useState<string>('');

  const handleExtract = async () => {
    if (!text.trim()) { message.warning('请输入文本'); return; }
    setLoading(true);
    try {
      const res = await extractionApi.extract(text);
      setResult(res.data);
      message.success('抽取完成');
    } catch { message.error('抽取失败'); }
    setLoading(false);
  };

  const handleBatch = async () => {
    const valid = conversations.filter(c => c.content.trim());
    if (valid.length === 0) { message.warning('请至少输入一条对话'); return; }
    setLoading(true);
    try {
      const res = await extractionApi.batchExtract(valid.map(c => ({ ...c, timestamp: new Date().toISOString() })));
      setBatchResult(res.data);
      message.success('批量抽取完成');
    } catch { message.error('批量抽取失败'); }
    setLoading(false);
  };

  const handleSummary = async () => {
    setLoading(true);
    try {
      const res = await extractionApi.summary();
      const data = res.data;
      // 后端返回 { summary: string, variable_count: number, success: boolean }
      if (typeof data === 'string') {
        setSummary(data);
      } else if (data?.summary) {
        setSummary(data.summary);
      } else {
        setSummary(JSON.stringify(data, null, 2));
      }
    }
    catch { message.error('获取失败'); }
    setLoading(false);
  };

  const handleContext = async () => {
    setLoading(true);
    try {
      const res = await extractionApi.context();
      const data = res.data;
      // 后端返回 { context: string, success: boolean }
      if (typeof data === 'string') {
        setContext(data);
      } else if (data?.context) {
        setContext(data.context);
      } else {
        setContext(JSON.stringify(data, null, 2));
      }
    }
    catch { message.error('获取失败'); }
    setLoading(false);
  };

  const renderExtracted = (data: Record<string, unknown> | null) => {
    if (!data) return <Empty description="尚未抽取" />;
    const info = data.extracted || data.memories || data;
    if (Array.isArray(info)) {
      return info.map((item: Record<string, unknown>, i: number) => (
        <Card key={i} size="small" style={{ marginBottom: 8 }}>
          <Descriptions size="small" column={2}>
            {Object.entries(item).map(([k, v]) => (
              <Descriptions.Item key={k} label={k}>{String(v)}</Descriptions.Item>
            ))}
          </Descriptions>
        </Card>
      ));
    }
    return <pre className="code-block">{JSON.stringify(info, null, 2)}</pre>;
  };

  return (
    <div>
      <div className="page-header">
        <h2><ThunderboltOutlined /> 记忆抽取</h2>
        <p>从自然语言中抽取结构化记忆：用户信息、偏好、计划、关键事实</p>
      </div>

      <Tabs items={[
        {
          key: 'single', label: '单段抽取',
          children: (
            <Spin spinning={loading}>
              <Card className="section-card">
                <Input.TextArea rows={5} value={text} onChange={e => setText(e.target.value)}
                  placeholder="输入一段文本，系统将自动抽取其中的关键信息...例如：我叫张三，喜欢打篮球，下周五要去北京出差。" />
                <div style={{ marginTop: 12 }}>
                  <Button type="primary" icon={<SendOutlined />} onClick={handleExtract}>抽取记忆</Button>
                </div>
              </Card>
              <Card title="抽取结果" className="section-card">{renderExtracted(result)}</Card>
            </Spin>
          ),
        },
        {
          key: 'batch', label: '批量抽取',
          children: (
            <Spin spinning={loading}>
              <Card className="section-card">
                {conversations.map((c, i) => (
                  <Space key={i} style={{ width: '100%', marginBottom: 8 }} align="start">
                    <Tag color={c.role === 'user' ? 'blue' : 'green'} style={{ minWidth: 60, textAlign: 'center' }}>{c.role === 'user' ? '用户' : '助手'}</Tag>
                    <Input.TextArea rows={2} value={c.content} onChange={e => {
                      const next = [...conversations]; next[i].content = e.target.value; setConversations(next);
                    }} placeholder={c.role === 'user' ? '用户输入...' : '助手回复...'} />
                    <Button danger size="small" onClick={() => setConversations(conversations.filter((_, j) => j !== i))}>×</Button>
                  </Space>
                ))}
                <Space style={{ marginTop: 8 }}>
                  <Button onClick={() => setConversations([...conversations, { role: 'user', content: '' }, { role: 'assistant', content: '' }])}>添加对话</Button>
                  <Button type="primary" icon={<SendOutlined />} onClick={handleBatch}>批量抽取</Button>
                </Space>
              </Card>
              {batchResult ? (
                <Card title="批量抽取结果" className="section-card">
                  <pre className="code-block">{JSON.stringify(batchResult, null, 2)}</pre>
                </Card>
              ) : null}
            </Spin>
          ),
        },
        {
          key: 'summary', label: '记忆摘要',
          children: (
            <Spin spinning={loading}>
              <Card className="section-card" extra={<Button onClick={handleSummary}>获取摘要</Button>}>
                {summary ? <pre className="code-block">{summary}</pre> : <Empty description="点击按钮获取用户记忆摘要" />}
              </Card>
            </Spin>
          ),
        },
        {
          key: 'context', label: '用户上下文',
          children: (
            <Spin spinning={loading}>
              <Card className="section-card" extra={<Button onClick={handleContext}>获取上下文</Button>}>
                {context ? <pre className="code-block">{context}</pre> : <Empty description="点击按钮获取用户上下文" />}
              </Card>
            </Spin>
          ),
        },
      ]} />
    </div>
  );
}
