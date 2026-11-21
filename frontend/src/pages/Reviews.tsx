import { isAdminPortal } from '../portal';
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Descriptions, Form, Input, Modal, Select, Space, Table, Tag, message } from 'antd';
import { get, post } from '../api/client';
import { PageTitle, Status } from '../components/Common';
import { useAuth } from '../stores/auth';
import { labels, weekdays, type Data } from '../types';

export default function Reviews() {
  const user = useAuth((s) => s.user)!;
  const query = useQuery({ queryKey: ['reviews', user.id], queryFn: () => get<Data[]>('/teachers/me/offering-reviews'), refetchInterval: 5000, enabled: user.role === 'TEACHER' });
  const [selected, setSelected] = useState<Data | null>(null);
  const [target, setTarget] = useState('REJECTED');
  const [reason, setReason] = useState('');
  const cache = useQueryClient();
  const save = useMutation({ mutationFn: (values: Data) => post('/teacher-decisions', values), onSuccess: () => { message.success('开课审核结果已保存'); setSelected(null); cache.invalidateQueries(); } });
  function decide(row: Data, decision: string) {
    if (decision === 'ACCEPTED') save.mutate({ decision_id: row.id, version: row.version, decision, reason: '' });
    else { setSelected(row); setTarget(decision); setReason(''); }
  }
  if (isAdminPortal && user.role === 'ADMIN') return <AdminReviews />;
  if (query.isError) return <Alert type="error" message="开课审核暂不可用" />;
  return <><PageTitle title="开课审核" description="确认授课意向；授课教师和时间将在排课、发布与确认后正式确定" />
    <Card><p className="table-scroll-hint">可左右滑动，查看完整审核信息与操作。</p><Table<Data> rowKey="id" scroll={{ x: 820 }} loading={query.isPending} dataSource={query.data || []} columns={[
      { title: '课程', render: (_, row) => <strong>{row.course.name}</strong> },
      { title: '上课周', render: (_, row) => row.offering.week }, { title: '学生人数', dataIndex: 'student_count' },
      { title: '时长', render: (_, row) => row.course.duration + ' 分钟' },
      { title: '审核结果', dataIndex: 'decision', render: (value, row) => <Space wrap><Status value={value} />{row.active_teaching && <Tag color="green">已生效授课</Tag>}</Space> },
      { title: '操作', render: (_, row) => <Space><Button type="link" onClick={() => decide(row, 'ACCEPTED')} disabled={row.decision === 'ACCEPTED'}>同意开课</Button><Button type="link" disabled={row.active_teaching} onClick={() => decide(row, 'ADJUST')}>调整意向</Button><Button type="link" danger disabled={row.active_teaching} onClick={() => decide(row, 'REJECTED')}>拒绝</Button></Space> },
    ]} expandable={{ expandedRowRender: (row) => <Descriptions size="small" column={2} items={[
      { key: 'description', label: '课程内容', children: row.course.description },
      { key: 'room', label: '建议教室类型', children: labels[row.course.preferred_room] || row.course.preferred_room },
      { key: 'features', label: '必需设备', children: row.required_features.map((feature: Data) => labels[feature.feature] || feature.feature).join('、') || '通用教学条件' },
      { key: 'ages', label: '年龄分布', children: Object.entries(row.age_distribution).map(([age, count]) => age + ' 岁 ' + count + ' 人').join('；') },
      { key: 'grades', label: '年级分布', children: Object.entries(row.grade_distribution).map(([grade, count]) => grade + ' 年级 ' + count + ' 人').join('；') },
      { key: 'preferences', label: '意向时段', children: Object.entries(row.preferences).map(([key, count]) => weekdays[Number(key.split(':')[0])] + ' ' + key.split(':')[1] + '（' + count + '人）').join('；') },
      { key: 'reason', label: '审核意见', children: row.reason || '—' },
    ]} /> }} /></Card>
    <Modal title={target === 'ADJUST' ? '说明调整意向' : '说明拒绝原因'} open={!!selected} onCancel={() => setSelected(null)} confirmLoading={save.isPending} okText="提交审核"
      onOk={() => reason.trim() ? save.mutate({ decision_id: selected!.id, version: selected!.version, decision: target, reason }) : message.warning('请填写原因或候选时间建议')}>
      <Input.TextArea rows={4} value={reason} maxLength={2000} onChange={(e) => setReason(e.target.value)} placeholder="填写原因及建议的候选时间，管理员可据此调整开课计划" />
    </Modal>
  </>;
}

function AdminReviews() {
  const cache = useQueryClient();
  const [form] = Form.useForm();
  const offeringId = Form.useWatch('offering_id', form);
  const reviews = useQuery({ queryKey: ['admin', 'teacher-decisions'], queryFn: () => get<Data>('/admin/teacher-decisions?page_size=500'), refetchInterval: 5000 });
  const offerings = useQuery({ queryKey: ['admin', 'offerings'], queryFn: () => get<Data>('/admin/offerings?page_size=500'), refetchInterval: 5000 });
  const teachers = useQuery({ queryKey: ['admin', 'review-teachers'], queryFn: () => get<Data>('/admin/teachers?page_size=500') });
  const offering = offerings.data?.items.find((item: Data) => item.id === offeringId);
  const invite = useMutation({ mutationFn: (values: Data) => post('/admin/offerings/' + values.offering_id + '/reviews', { teacher_ids: values.teacher_ids }),
    onSuccess: () => { message.success('审核邀请已发送'); form.resetFields(); cache.invalidateQueries(); } });
  if (reviews.isError || offerings.isError || teachers.isError) return <Alert type="error" message="开课审核管理暂不可用" />;
  return <><PageTitle title="开课审核管理" description="查看教师审核结果与原因，向具有课程资质的教师发送审核邀请" />
    <Card title="邀请教师审核" style={{ marginBottom: 20 }}>
      <Form form={form} layout="inline" onFinish={(values) => invite.mutate(values)}>
        <Form.Item name="offering_id" label="开课计划" rules={[{ required: true }]}><Select showSearch optionFilterProp="label" style={{ width: 330 }}
          onChange={() => form.setFieldValue('teacher_ids', [])}
          options={(offerings.data?.items || []).filter((item: Data) => item.status !== 'MERGED').map((item: Data) => ({ value: item.id, label: item.name }))} /></Form.Item>
        <Form.Item name="teacher_ids" label="具备资质教师" rules={[{ required: true }]}><Select mode="multiple" style={{ minWidth: 240 }} disabled={!offering}
          options={(teachers.data?.items || []).filter((item: Data) => item.active && item.course_ids.includes(offering?.course_id)).map((item: Data) => ({ value: item.user_id, label: item.name }))} /></Form.Item>
        <Button type="primary" htmlType="submit" loading={invite.isPending}>发送审核邀请</Button>
      </Form>
    </Card>
    <Card><Table<Data> rowKey="id" scroll={{ x: 800 }} loading={reviews.isPending} dataSource={reviews.data?.items || []} columns={[
      { title: '课程', dataIndex: 'course_name' }, { title: '上课周', dataIndex: 'week' }, { title: '教师', dataIndex: 'teacher_name' },
      { title: '计划状态', dataIndex: 'offering_status', render: (value) => <Status value={value} /> },
      { title: '审核结果', dataIndex: 'decision', render: (value) => <Status value={value} /> },
      { title: '原因与建议', dataIndex: 'reason', render: (value) => value || '—' },
    ]} /></Card>
  </>;
}
