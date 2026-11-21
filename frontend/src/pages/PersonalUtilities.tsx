import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Checkbox, Descriptions, List, Space, Table, message } from 'antd';
import dayjs from 'dayjs';
import { get, post } from '../api/client';
import { useBootstrap } from '../hooks/useBootstrap';
import { useAuth } from '../stores/auth';
import { Loading, PageTitle } from '../components/Common';
import { labels, weekdays, type Data } from '../types';
export function Notifications() {
  const user = useAuth((s) => s.user)!;
  const cache = useQueryClient();
  const query = useQuery({ queryKey: ['notifications', user.id], queryFn: () => get<Data[]>('/notifications'), refetchInterval: 5000 });
  const read = useMutation({ mutationFn: (id: string) => post('/notifications/' + id + '/read'),
    onSuccess: () => cache.invalidateQueries({ queryKey: ['notifications'] }) });
  return <><PageTitle title="通知中心" description="查看本人课程、审核与异常事务通知" /><Card><List loading={query.isPending} dataSource={query.data || []} renderItem={(item) => <List.Item actions={!item.read_at ? [<Button type="link" key="read" onClick={() => read.mutate(item.id)}>标记已读</Button>] : []}>
    <List.Item.Meta title={<Space>{!item.read_at && <span style={{ color: '#346db7' }}>●</span>}{item.title}</Space>} description={<>{item.content}<br /><small>{dayjs(item.created_at).format('YYYY-MM-DD HH:mm')}</small></>} />
  </List.Item>} /></Card></>;
}
export function Profile() {
  const user = useAuth((s) => s.user)!;
  const cache = useQueryClient();
  const { data: bootstrap } = useBootstrap();
  const query = useQuery({ queryKey: ['profile', user.id], queryFn: () => get('/profile') });
  const [selection, setSelection] = useState<string[] | null>(null);
  const chosen = selection || query.data?.availability.map((s: Data) => s.day_index + ':' + s.slot_code) || [];
  const save = useMutation({ mutationFn: () => post('/availability/me', { slots: chosen.map((key: string) => {
    const [day, code] = key.split(':'); return { day_index: Number(day), slot_code: code };
  }) }), onSuccess: () => { message.success('可用时段已保存'); setSelection(null); cache.invalidateQueries(); } });
  if (query.isError) return <Alert type="error" message="个人信息暂不可用" />;
  if (query.isPending || !bootstrap) return <Loading />;
  return <div className="section-stack"><PageTitle title="个人信息与可用时段" description="维护本人可用时间；已生效课程的变化请通过异常申请处理" />
    <Card title="基本信息"><Descriptions column={3} items={[
      { key: 'name', label: '姓名', children: query.data.user.name }, { key: 'role', label: '身份', children: labels[user.role] },
      { key: 'account', label: '账号', children: query.data.user.username }, { key: 'phone', label: '联系方式', children: query.data.user.phone },
      ...(user.role === 'STUDENT' ? [{ key: 'age', label: '年龄', children: query.data.profile?.age }, { key: 'grade', label: '年级', children: query.data.profile?.grade }] : []),
    ]} /></Card>
    <Card title="每周可用时段" extra={<Button type="primary" loading={save.isPending} onClick={() => save.mutate()}>保存可用时间</Button>}>
      <Checkbox.Group value={chosen} onChange={(values) => setSelection(values as string[])}>
        <Space direction="vertical" size={18}>{weekdays.map((day, i) => <div key={day}><strong style={{ display: 'inline-block', width: 50 }}>{day}</strong>{bootstrap.slots.map((slot: Data) => <Checkbox key={slot.code} value={i + ':' + slot.code}>{slot.code} {slot.start}</Checkbox>)}</div>)}</Space>
      </Checkbox.Group>
    </Card>
    {user.role === 'STUDENT' && <Card title="课程反馈记录"><Table<Data> rowKey="id" dataSource={query.data.feedback || []} columns={[
      { title: '教学反馈', dataIndex: 'content' }, { title: '评分', dataIndex: 'rating' },
      { title: '更新时间', dataIndex: 'updated_at', render: (value) => dayjs(value).format('MM-DD HH:mm') },
    ]} /></Card>}
  </div>;
}
