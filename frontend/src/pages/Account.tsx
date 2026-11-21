import { lazy, useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Form, Input, List, Space, Tag, message } from 'antd';
import { Link, useNavigate } from 'react-router-dom';
import dayjs from 'dayjs';
import { authClient, get, post } from '../api/client';
import { useAuth } from '../stores/auth';
import { PageTitle } from '../components/Common';
import { isAdminPortal } from '../portal';
import type { Data } from '../types';
const Administration = isAdminPortal ? lazy(() => import('./AdminAccounts')) : null;

export default function Account() {
  const user = useAuth((s) => s.user)!;
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const cache = useQueryClient();
  const sessions = useQuery({ queryKey: ['auth-sessions', user.id], queryFn: () => get<Data[]>('/auth/sessions') });
  async function password(values: Data) {
    setBusy(true);
    try {
      await post('/auth/password', values);
      useAuth.getState().clear(); cache.clear(); navigate('/login', { replace: true });
      message.success('密码已更新，所有旧会话已撤销，请重新登录');
    } finally { setBusy(false); }
  }
  return <div className="section-stack"><PageTitle title="账号与安全" description="修改密码、查看会话和管理登录权限" />
    <Card title="修改本人密码"><Form layout="vertical" onFinish={password} style={{ maxWidth: 480 }}>
      <Form.Item name="current_password" label="原密码" rules={[{ required: true }]}><Input.Password autoComplete="current-password" maxLength={128} /></Form.Item>
      <Form.Item name="new_password" label="新密码" rules={[{ required: true }, { min: 12, message: '请设置至少 12 位密码' }]}><Input.Password autoComplete="new-password" maxLength={128} /></Form.Item>
      <Button type="primary" htmlType="submit" loading={busy}>保存并撤销旧会话</Button>
    </Form></Card>
    <Card title="本人登录会话"><List loading={sessions.isPending} dataSource={sessions.data || []} renderItem={(item) => <List.Item actions={!item.revoked_at ? [<Button key="revoke" onClick={async () => { await post('/auth/sessions/' + item.id + '/revoke'); await sessions.refetch(); }}>撤销</Button>] : []}>
      <List.Item.Meta title={<Space><Tag>{item.portal}</Tag>{item.revoked_at ? '已撤销' : dayjs(item.expires_at).isBefore(dayjs()) ? '已过期' : '有效'}</Space>} description={'创建于 ' + dayjs(item.created_at).format('YYYY-MM-DD HH:mm') + ' · 到期 ' + dayjs(item.expires_at).format('MM-DD HH:mm')} />
    </List.Item>} /></Card>
    {Administration && user.role === 'ADMIN' && <Administration />}
  </div>;
}

export function AcceptAccount() {
  const [credential] = useState(() => new URLSearchParams(window.location.hash.slice(1)));
  const [busy, setBusy] = useState(false);
  const [accepted, setAccepted] = useState(false);
  const token = credential.get('token');
  const purpose = credential.get('purpose');
  useEffect(() => { window.history.replaceState(null, '', '/accept'); }, []);
  async function accept(values: Data) {
    setBusy(true);
    try { await authClient.post('/auth/accept', { token, purpose, password: values.password }); setAccepted(true); }
    catch (error: any) { message.error(error.response?.data?.title || '链接无法使用'); }
    finally { setBusy(false); }
  }
  return <main className="accept-page"><Card title={purpose === 'RESET' ? '重置账号密码' : '接受机构邀请'} style={{ width: 440, maxWidth: '100%' }}>
    {accepted ? <Alert type="success" showIcon message="密码设置成功" description={<Link to="/login">返回登录页</Link>} /> : !token || !['INVITE', 'RESET'].includes(purpose || '') ? <Alert type="warning" message="请使用管理员提供的完整链接" description="链接一次有效，不包含在网址查询参数或服务器访问日志中。" /> : <Form layout="vertical" onFinish={accept}>
      <p>请为自己的账号设置密码。链接使用后立即失效。</p>
      <Form.Item name="password" label="新密码" rules={[{ required: true }, { min: 12, message: '至少 12 位' }]}><Input.Password autoComplete="new-password" maxLength={128} /></Form.Item>
      <Button type="primary" htmlType="submit" loading={busy}>确认设置密码</Button>
    </Form>}
  </Card></main>;
}
