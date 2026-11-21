import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Form, Input, Modal, Rate, Select, Space, Table, message } from 'antd';
import { get, post } from '../api/client';
import { useBootstrap } from '../hooks/useBootstrap';
import { useAuth } from '../stores/auth';
import { PageTitle, Status } from '../components/Common';
import { labels, type Data, type Lesson, type Schedule } from '../types';

export default function Classes() {
  const user = useAuth((s) => s.user)!;
  const { data: bootstrap } = useBootstrap();
  const [selected, setSelected] = useState<Lesson | null>(null);
  const [feedbackStudent, setFeedbackStudent] = useState<Data | null>(null);
  const [statuses, setStatuses] = useState<Record<string, string>>({});
  const [form] = Form.useForm();
  const cache = useQueryClient();
  const schedule = useQuery({
    queryKey: ['my-classes', user.id, bootstrap?.default_week],
    queryFn: () => get<Schedule>('/teachers/me/schedule', { week: bootstrap?.default_week }),
    enabled: !!bootstrap, refetchInterval: 5000,
  });
  const roster = useQuery({
    queryKey: ['roster', selected?.record_id], queryFn: () => get('/classes/' + selected!.record_id + '/roster'),
    enabled: !!selected,
  });
  const attendance = useMutation({ mutationFn: () => post('/attendance/batch', {
    records: roster.data!.students.map((student: Data) => ({
      session_id: selected!.record_id, student_id: student.id,
      status: statuses[student.id] || student.attendance[0]?.status || 'PRESENT', notes: '',
    })),
  }), onSuccess: () => { message.success('本班出勤已保存'); cache.invalidateQueries(); } });
  const feedback = useMutation({ mutationFn: (values: Data) => post('/feedback', {
    session_id: selected!.record_id, student_id: feedbackStudent!.id, ...values,
  }), onSuccess: () => { setFeedbackStudent(null); cache.invalidateQueries(); message.success('课程反馈已保存并通知学生'); } });
  if (schedule.isError) return <Alert type="error" message="无法读取本人班级" />;
  const lessons = schedule.data?.sessions.filter((s) => s.version_status === 'ACTIVE' && s.teacher_id === user.id && s.status === 'SCHEDULED') || [];
  return <><PageTitle title="我的班级" description="仅显示本人负责的生效班次；可登记出勤与课程反馈" />
    <Card><Table<Lesson> rowKey="record_id" loading={schedule.isPending} dataSource={lessons} columns={[
      { title: '课程', dataIndex: 'course_name' }, { title: '日期', dataIndex: 'day' },
      { title: '时段', render: (_, row) => row.slot_code + ' ' + row.start_time + '—' + row.end_time },
      { title: '教室', dataIndex: 'room_name' }, { title: '学生人数', dataIndex: 'student_count' },
      { title: '操作', render: (_, row) => <Button type="link" onClick={() => { setSelected(row); setStatuses({}); }}>班级名单与出勤</Button> },
    ]} /></Card>
    <Modal title={selected?.course_name + ' · ' + selected?.day + ' ' + selected?.slot_code} width={900} open={!!selected} onCancel={() => setSelected(null)}
      footer={<Space><Button onClick={() => setSelected(null)}>关闭</Button><Button type="primary" loading={attendance.isPending} disabled={!roster.data} onClick={() => attendance.mutate()}>保存本班出勤</Button></Space>}>
      <Table<Data> rowKey="id" loading={roster.isPending} dataSource={roster.data?.students || []} pagination={{ pageSize: 10 }} columns={[
        { title: '学生姓名', dataIndex: 'name' }, { title: '年龄', dataIndex: 'age' }, { title: '年级', dataIndex: 'grade' },
        { title: '本次出勤', render: (_, student) => <Select style={{ width: 120 }} value={statuses[student.id] || student.attendance[0]?.status || 'PRESENT'}
          onChange={(value) => setStatuses((previous) => ({ ...previous, [student.id]: value }))}
          options={['PRESENT', 'ABSENT', 'LATE', 'EXCUSED'].map((value) => ({ value, label: labels[value] }))} /> },
        { title: '记录状态', render: (_, student) => student.attendance.length ? <Status value={student.attendance[0].status} /> : <span className="muted">尚未保存</span> },
        { title: '课程反馈', render: (_, student) => <Button type="link" onClick={() => { setFeedbackStudent(student); form.resetFields(); form.setFieldsValue(student.feedback[0] || { rating: 5 }); }}>登记反馈</Button> },
      ]} />
    </Modal>
    <Modal title={'课程反馈 · ' + feedbackStudent?.name} open={!!feedbackStudent} onCancel={() => setFeedbackStudent(null)} onOk={() => form.submit()} okText="保存并通知学生" confirmLoading={feedback.isPending}>
      <Form form={form} layout="vertical" onFinish={(values) => feedback.mutate(values)}>
        <Form.Item name="rating" label="学习表现" rules={[{ required: true }]}><Rate /></Form.Item>
        <Form.Item name="content" label="教学反馈" rules={[{ required: true }]}><Input.TextArea rows={4} maxLength={4000} /></Form.Item>
      </Form>
    </Modal>
  </>;
}
