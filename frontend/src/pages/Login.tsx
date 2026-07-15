import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Form, Input, Button, Tabs, message } from 'antd';
import { useAuthStore } from '../stores/authStore';

export default function LoginPage() {
  const [tab, setTab] = useState<'login' | 'register'>('login');
  const { login, register, loading } = useAuthStore();
  const navigate = useNavigate();

  const onFinish = async (values: { username: string; password: string; email?: string }) => {
    try {
      if (tab === 'login') {
        await login(values.username, values.password);
      } else {
        await register(values.username, values.password, values.email);
      }
      message.success(tab === 'login' ? '登录成功' : '注册成功');
      navigate('/');
    } catch (e: unknown) {
      message.error((e as Error).message);
    }
  };

  return (
    <div className="auth-container">
      <div className="auth-card">
        <h1>{tab === 'login' ? '登录' : '注册'}</h1>
        <p className="subtitle">Agent Memory System</p>
        <Tabs activeKey={tab} onChange={(k) => setTab(k as 'login' | 'register')} centered items={[
          { key: 'login', label: '登录' },
          { key: 'register', label: '注册' },
        ]} />
        <Form layout="vertical" onFinish={onFinish} autoComplete="off">
          <Form.Item name="username" rules={[{ required: true, message: '请输入用户名' }]}>
            <Input placeholder="用户名" size="large" />
          </Form.Item>
          <Form.Item name="password" rules={[{ required: true, message: '请输入密码' }, { min: 6, message: '密码至少6位' }]}>
            <Input.Password placeholder="密码" size="large" />
          </Form.Item>
          {tab === 'register' && (
            <Form.Item name="email">
              <Input placeholder="邮箱（选填）" size="large" />
            </Form.Item>
          )}
          <Form.Item>
            <Button type="primary" htmlType="submit" loading={loading} block size="large">
              {tab === 'login' ? '登录' : '注册'}
            </Button>
          </Form.Item>
        </Form>
      </div>
    </div>
  );
}
