import { useState } from 'react';
import { Button, Card, Form, Input, Segmented, Space, Typography, message } from 'antd';
import { CalendarOutlined, LockOutlined, SafetyCertificateOutlined, UserOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { authClient } from '../api/client';
import { useAuth } from '../stores/auth';
import type { Role } from '../types';
import { portal } from '../portal';

export default function Login() {
  const [role, setRole] = useState<Role>(portal === 'PUBLIC' ? 'STUDENT' : 'ADMIN');
  const [busy, setBusy] = useState(false);
  const [form] = Form.useForm();
  const navigate = useNavigate();
  const cache = useQueryClient();
  const configuration = useQuery({ queryKey: ['login-configuration'], queryFn: async ({ signal }) => (await authClient.get('/auth/config', { signal })).data, retry: 1 });
  async function login(values: { username: string; password: string }) {
    setBusy(true);
    try {
      const { data } = await authClient.post('/auth/login', { ...values, role });
      cache.clear();
      useAuth.getState().setSession(data.user, data.access_token);
      navigate('/', { replace: true });
    } catch (error: any) { message.error(error.response?.data?.title || '无法连接服务，请检查后端是否启动'); }
    finally { setBusy(false); }
  }
  return <main className="login-page">
    <section className="login-story">
      <div className="brand-mark">E<span>EduFlow <b>AI</b></span></div>
      <div className="login-copy"><Tagline /><h1>让每一节课，<br />都有更好的安排。</h1><p>连接学生、教师与教学资源，<br />让选课、排课和日常运营有序发生。</p>
        <Space size={24} wrap><span><CalendarOutlined /> 智能周课表</span><span><SafetyCertificateOutlined /> 双向确认</span></Space>
      </div><small>EduFlow AI V2.0 · 教培智能运营平台</small>
    </section>
    <section className="login-form-area"><Card className="login-card" bordered={false}>
      <Typography.Title level={2}>欢迎回来</Typography.Title><Typography.Paragraph type="secondary">选择你的身份，进入专属工作台</Typography.Paragraph>
      <Segmented block value={role} options={portal === 'ADMIN' ? [{ label: '管理员', value: 'ADMIN' }] : portal === 'PUBLIC' ? [{ label: '学生', value: 'STUDENT' }, { label: '教师', value: 'TEACHER' }] : [{ label: '管理员', value: 'ADMIN' }, { label: '教师', value: 'TEACHER' }, { label: '学生', value: 'STUDENT' }]} onChange={(v) => setRole(v as Role)} />
      <Form form={form} layout="vertical" onFinish={login} style={{ marginTop: 28 }} requiredMark={false}>
        <Form.Item name="username" label="账号" rules={[{ required: true, message: '请输入账号' }]}><Input size="large" prefix={<UserOutlined />} autoComplete="username" placeholder="请输入账号" /></Form.Item>
        <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}><Input.Password size="large" prefix={<LockOutlined />} autoComplete="current-password" placeholder="请输入密码" /></Form.Item>
        <Button type="primary" block size="large" htmlType="submit" loading={busy}>登录工作台</Button>
      </Form>
      <div className="demo-login"><Typography.Text type="secondary">{configuration.data?.demo_mode ? '本地模拟数据 · 公网关闭' : '机构独立账号 · 公网关闭'}</Typography.Text>{configuration.data?.demo_mode && <Button type="link" size="small" onClick={() => form.setFieldsValue({
        username: role === 'ADMIN' ? 'admin' : role === 'TEACHER' ? 'teacher01' : 'student001',
        password: 'EduFlow-Demo-2026!',
      })}>填入演示账号</Button>}</div>
    </Card><small className="login-footer">课程安排以正式生效的课表为准</small></section>
  </main>;
}
function Tagline() { return <div className="login-eyebrow">更轻松的教学运营</div>; }
