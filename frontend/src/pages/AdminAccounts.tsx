import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Card, Form, Input, InputNumber, Modal, Select, Space, Table, Tag, message } from 'antd';
import { get, post } from '../api/client';
import { useBootstrap } from '../hooks/useBootstrap';
import { labels, type Data } from '../types';

export default function AdminAccounts() {
  const [page, setPage] = useState(1);
  const [invite, setInvite] = useState(false);
  const [link, setLink] = useState('');
  const [busy, setBusy] = useState(false);
  const [form] = Form.useForm();
  const role = Form.useWatch('role', form);
  const { data: bootstrap } = useBootstrap();
  const query = useQuery({ queryKey: ['accounts', page], queryFn: () => get('/admin/users', { page, page_size: 20 }) });
  function showLink(value: Data) {
    const origin = value.portal === 'ADMIN' ? bootstrap?.admin_portal_origin || window.location.origin : bootstrap?.public_portal_origin || 'http://127.0.0.1:8100';
    setLink(origin + '/accept#' + new URLSearchParams({ token: value.token, purpose: value.purpose }));
  }
  async function create(values: Data) {
    setBusy(true);
    try { showLink(await post('/admin/invitations', values)); setInvite(false); form.resetFields(); await query.refetch(); }
    finally { setBusy(false); }
  }
  return <Card title="机构账号管理" extra={<Button type="primary" onClick={() => setInvite(true)}>邀请师生</Button>}>
    <Alert type="info" showIcon message="邀请与重置链接有效期 24 小时，仅展示一次；通过可信方式交给本人。" style={{ marginBottom: 16 }} />
    <Table<Data> rowKey="id" loading={query.isPending} dataSource={query.data?.items || []} scroll={{ x: 680 }} columns={[
      { title: '账号', dataIndex: 'username' }, { title: '姓名', dataIndex: 'name' },
      { title: '身份', dataIndex: 'role', render: (value) => labels[value] },
      { title: '状态', dataIndex: 'active', render: (value) => <Tag>{value ? '启用' : '停用 / 待邀请'}</Tag> },
      { title: '操作', render: (_, item) => <Space wrap>
        {item.active && <Button danger onClick={() => Modal.confirm({ title: '停用 ' + item.name + ' 的账号？', content: '现有登录会话将被撤销；涉及已生效课程的账号需先处理课程。', onOk: async () => { await post('/admin/users', { id: item.id, version: item.version, username: item.username, name: item.name, role: item.role, phone: item.phone, active: false }); await query.refetch(); } })}>停用账号</Button>}
        <Button onClick={async () => { showLink(await post('/admin/users/' + item.id + '/account-token', { purpose: item.active ? 'RESET' : 'INVITE' })); }}>{item.active ? '重置密码' : '生成邀请'}</Button>
        <Button onClick={async () => { await post('/admin/users/' + item.id + '/revoke-sessions'); message.success('旧会话已撤销'); }}>撤销会话</Button>
      </Space> },
    ]} pagination={{ current: page, pageSize: 20, total: query.data?.total || 0, onChange: setPage }} />
    <Modal title="邀请新师生" open={invite} onCancel={() => setInvite(false)} onOk={() => form.submit()} confirmLoading={busy}>
      <Form form={form} layout="vertical" onFinish={create} initialValues={{ role: 'STUDENT', age: 10, grade: 5 }}>
        <Form.Item name="username" label="登录账号" rules={[{ required: true }, { pattern: /^[A-Za-z0-9_.-]{3,60}$/ }]}><Input autoComplete="off" /></Form.Item>
        <Form.Item name="name" label="姓名" rules={[{ required: true }]}><Input maxLength={80} /></Form.Item>
        <Form.Item name="role" label="身份"><Select options={[{ value: 'STUDENT', label: '学生' }, { value: 'TEACHER', label: '教师' }]} /></Form.Item>
        {role === 'STUDENT' && <Space><Form.Item name="age" label="年龄"><InputNumber min={3} max={100} /></Form.Item><Form.Item name="grade" label="年级"><InputNumber min={0} max={20} /></Form.Item></Space>}
      </Form>
    </Modal>
    <Modal title="账号链接" open={!!link} onCancel={() => setLink('')} footer={<Button onClick={() => setLink('')}>已保存，关闭</Button>} destroyOnHidden>
      <Input.TextArea aria-label="一次性账号链接" value={link} readOnly autoSize={{ minRows: 3 }} />
      <Button style={{ marginTop: 12 }} onClick={async () => { try { await navigator.clipboard.writeText(link); message.success('链接已复制'); } catch { message.info('请手动复制上方链接'); } }}>复制链接</Button>
    </Modal>
  </Card>;
}
