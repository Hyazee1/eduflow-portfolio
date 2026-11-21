import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Checkbox, DatePicker, Descriptions, Form, Modal, Space, Tabs, Tag, message } from 'antd';
import dayjs from 'dayjs';
import { get, post } from '../api/client';
import { useBootstrap } from '../hooks/useBootstrap';
import { groups, labels, weekdays, type Data, type Slot } from '../types';
import { Loading, PageTitle } from '../components/Common';

export default function Courses() {
  const { data: bootstrap } = useBootstrap();
  const { data: courses, isPending, isError } = useQuery({ queryKey: ['courses'], queryFn: () => get<Data[]>('/courses'), refetchInterval: 5000 });
  const [group, setGroup] = useState('MAIN');
  const [chosen, setChosen] = useState<Data | null>(null);
  const [form] = Form.useForm();
  const cache = useQueryClient();
  const submit = useMutation({ mutationFn: async (values: Data) => post('/enrollment-requests', {
    course_id: chosen!.id, week: values.week.startOf('isoWeek').format('YYYY-MM-DD'),
    preferences: values.preferences.map((key: string) => { const [day, code] = key.split(':'); return { day_index: Number(day), slot_code: code }; }),
  }), onSuccess: () => { message.success('选课申请已提交，等待教师审核'); setChosen(null); cache.invalidateQueries(); } });
  if (isError) return <Alert type="error" message="课程目录暂不可用" />;
  if (isPending || !bootstrap) return <Loading />;
  const slots = (bootstrap.slots as Slot[]).filter((s) => chosen?.course_group === 'QA' ? s.kind === 'QA' : s.kind === 'REGULAR');
  return <><PageTitle title="课程选课" description="选择课程类别与时间偏好，提交后可跟踪教师审核进度" />
    <Tabs className="course-group-tabs" activeKey={group} onChange={setGroup} items={groups.map((key) => ({ key, label: labels[key] + '组' }))} />
    <div className="course-grid">{courses.filter((c) => c.course_group === group).map((course) => <Card key={course.id} className="course-card">
      <div><Tag color={group === 'QA' ? 'gold' : 'blue'}>{labels[group]}</Tag><Tag>{course.duration} 分钟</Tag></div>
      <h3 style={{ marginTop: 16 }}>{course.name}</h3><p>{course.description}</p>
      <div className="course-meta">{course.min_age}—{course.max_age} 岁 · {course.min_grade}—{course.max_grade} 年级<br />
        班次人数 {course.min_size}—{course.max_size} 人<br />{course.teacher_name} · {course.room_name}<br />
        {course.features?.length > 0 && <>设备条件：{(course.features as string[]).map((feature) => labels[feature] || feature).join('、')}<br /></>}
        {course.provisional ? '尚无正式生效班次' : (course.classes as Data[]).map((lesson) => <div key={lesson.id}>
          {lesson.day} {lesson.slot_code} {lesson.start}—{lesson.end} · 余位 {lesson.remaining_seats}
        </div>)}</div>
      <Button onClick={() => { setChosen(course); form.setFieldsValue({ week: dayjs(bootstrap.default_week), preferences: [] }); }}>选择课程</Button>
    </Card>)}</div>
    <Modal title={'选课申请 · ' + (chosen?.name || '')} open={!!chosen} onCancel={() => setChosen(null)} onOk={() => form.submit()} confirmLoading={submit.isPending} okText="确认并提交申请" width={720}>
      <Alert type="info" showIcon message="时间偏好用于优化排课；最终教师、教室与时间以双方确认后的正式课表为准。" style={{ marginBottom: 20 }} />
      <Form form={form} layout="vertical" onFinish={(values) => submit.mutate(values)}>
        <Form.Item name="week" label="上课周" rules={[{ required: true }]}><DatePicker picker="week" allowClear={false} /></Form.Item>
        <Form.Item name="preferences" label="选择意向时段（可多选）" rules={[{ required: true, message: '至少选择一个意向时段' }]}>
          <Checkbox.Group><Space direction="vertical" size={12}>{weekdays.map((day, index) => <div key={day}><strong style={{ display: 'inline-block', width: 48 }}>{day}</strong>{slots.map((slot) => <Checkbox key={slot.code} value={index + ':' + slot.code}>{slot.code} {slot.start}</Checkbox>)}</div>)}</Space></Checkbox.Group>
        </Form.Item>
      </Form><Descriptions size="small" column={2} items={[{ key: 'duration', label: '时长', children: (chosen?.duration || '') + ' 分钟' }, { key: 'capacity', label: '班次容量', children: chosen?.max_size + ' 人' }]} />
    </Modal>
  </>;
}
