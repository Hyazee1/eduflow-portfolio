import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Checkbox, Col, Form, Input, InputNumber, Modal, Row, Select, Space, Switch, Table, Tag, message } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { get, post } from '../api/client';
import { groups, labels, weekdays, type Data } from '../types';
import { PageTitle, Status } from '../components/Common';

const featureOptions = [
  { label: '计算机', value: 'computer' }, { label: '实验器材', value: 'lab' },
  { label: '安全设施', value: 'safety' }, { label: '无人机', value: 'drone' },
  { label: '艺术绘画', value: 'art' }, { label: '书法', value: 'calligraphy' },
  { label: '机械器材', value: 'mechanics' },
];
const roomOptions = [
  ['COMPUTER', '计算机教室'], ['LAB', '科学实验室'], ['DRONE', '无人机与机器人实验室'],
  ['ART', '艺术绘画教室'], ['CALLIGRAPHY', '书法教室'], ['GENERAL', '普通教学教室'], ['MULTIPURPOSE', '多功能教室'],
].map(([value, label]) => ({ value, label }));

export default function Resources({ kind }: { kind: 'students' | 'teachers' | 'catalog' | 'rooms' }) {
  const entity = kind === 'catalog' ? 'courses' : kind;
  const title = { students: '学生 CRM', teachers: '教师管理', catalog: '课程管理', rooms: '教室资源' }[kind];
  const isUser = kind === 'students' || kind === 'teachers';
  const role = kind === 'students' ? 'STUDENT' : 'TEACHER';
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [search, setSearch] = useState('');
  const [editing, setEditing] = useState<Data | null>(null);
  const [qualification, setQualification] = useState<Data | null>(null);
  const [hoursFor, setHoursFor] = useState<Data | null>(null);
  const [courseIds, setCourseIds] = useState<string[]>([]);
  const [form] = Form.useForm();
  const cache = useQueryClient();
  const query = useQuery({ queryKey: ['resources', entity, page, pageSize, search], queryFn: () => get('/admin/' + entity, { page, page_size: pageSize, search }) });
  const { data: courses = [] } = useQuery({ queryKey: ['courses'], queryFn: () => get<Data[]>('/courses'), enabled: kind === 'teachers' });
  const save = useMutation({ mutationFn: async (values: Data) => {
    if (isUser) {
      const payload: Data = { ...values, role, id: editing?.user_id, version: editing?.user_version || 1 };
      if (!payload.password) delete payload.password;
      return post('/admin/users', payload);
    }
    const { features, ...valuesOnly } = values;
    return post('/admin/resources/' + entity, {
      id: editing?.id, version: editing?.version || 1, values: valuesOnly, features: features || [],
    });
  }, onSuccess: () => { message.success('已保存'); setEditing(null); cache.invalidateQueries(); } });
  const saveQualification = useMutation({ mutationFn: () => post('/admin/teachers/' + qualification!.user_id + '/qualifications', { course_ids: courseIds }),
    onSuccess: () => { setQualification(null); cache.invalidateQueries(); message.success('授课资质已保存'); } });
  function edit(row: Data = {}) {
    setEditing(row); form.resetFields();
    form.setFieldsValue({
      active: true, age: 10, grade: 5, min_age: 6, max_age: 16, min_grade: 1, max_grade: 10,
      course_group: 'MAIN', duration: 45, min_size: 3, max_size: 20, status: 'OPEN',
      kind: 'GENERAL', capacity: 20, preferred_room: 'GENERAL', features: [], ...row,
    });
  }
  const columns: Data[] = isUser ? [
    { title: '姓名', dataIndex: 'name', render: (name: string) => <strong>{name}</strong> },
    { title: '账号', dataIndex: 'username' },
    { title: '编号', dataIndex: kind === 'students' ? 'student_code' : 'teacher_code' },
    ...(kind === 'students' ? [{ title: '年龄', dataIndex: 'age' }, { title: '年级', dataIndex: 'grade' }] : [
      { title: '授课资质', render: (_: unknown, row: Data) => <Tag>{row.course_ids?.length || 0} 项课程</Tag> },
    ]),
    { title: '联系方式', dataIndex: 'phone' },
    { title: '状态', render: (_: unknown, row: Data) => <Tag color={row.active ? 'green' : 'default'}>{row.active ? '启用' : '停用'}</Tag> },
  ] : kind === 'catalog' ? [
    { title: '课程名称', dataIndex: 'name', render: (name: string) => <strong>{name}</strong> },
    { title: '分组', dataIndex: 'course_group', render: (value: string) => labels[value] },
    { title: '时长', dataIndex: 'duration', render: (value: number) => value + ' 分钟' },
    { title: '班次人数', render: (_: unknown, row: Data) => row.min_size + '—' + row.max_size + ' 人' },
    { title: '年龄', render: (_: unknown, row: Data) => row.min_age + '—' + row.max_age + ' 岁' },
    { title: '状态', dataIndex: 'status', render: (value: string) => <Status value={value} /> },
  ] : [
    { title: '教室名称', dataIndex: 'name', render: (name: string) => <strong>{name}</strong> },
    { title: '类型', dataIndex: 'kind', render: (value: string) => roomOptions.find((v) => v.value === value)?.label || value },
    { title: '容量', dataIndex: 'capacity', render: (value: number) => value + ' 人' },
    { title: '设备', dataIndex: 'features', render: (values: string[]) => values?.map((v) => <Tag key={v}>{featureOptions.find((f) => f.value === v)?.label || v}</Tag>) },
    { title: '状态', dataIndex: 'status', render: (value: string) => <Status value={value} /> },
  ];
  columns.push({ title: '操作', fixed: 'right', width: ['teachers', 'rooms'].includes(kind) ? 175 : 90, render: (_: unknown, row: Data) => <Space className="table-actions">
    <Button type="link" size="small" onClick={() => edit(row)}>编辑</Button>{kind === 'teachers' && <Button type="link" size="small" onClick={() => { setQualification(row); setCourseIds(row.course_ids || []); }}>授课资质</Button>}
    {kind === 'rooms' && <Button type="link" size="small" onClick={() => setHoursFor(row)}>开放时段</Button>}
  </Space> });
  if (query.isError) return <Alert type="error" message="无法读取资源列表" />;
  return <><PageTitle title={title} description="维护教学业务资料，所有调整均进行后端校验并记录审计"
    actions={<Button type="primary" icon={<PlusOutlined />} onClick={() => edit()}>新增{kind === 'students' ? '学生' : kind === 'teachers' ? '教师' : kind === 'catalog' ? '课程' : '教室'}</Button>} />
    <Card><Input.Search placeholder="搜索姓名、账号或名称" allowClear style={{ width: 300, marginBottom: 20 }} onSearch={(value) => { setSearch(value); setPage(1); }} />
      <Table rowKey="id" loading={query.isPending} dataSource={query.data?.items || []} columns={columns}
        scroll={{ x: 900 }} pagination={{ current: page, pageSize, total: query.data?.total || 0, showSizeChanger: true,
          showTotal: (total) => '共 ' + total + ' 条', onChange: (p, size) => { setPage(p); setPageSize(size); } }} />
    </Card>
    <Modal title={(editing?.id ? '编辑' : '新增') + title} open={editing !== null} onCancel={() => setEditing(null)} onOk={() => form.submit()} confirmLoading={save.isPending} width={720} okText="保存">
      <Form form={form} layout="vertical" onFinish={(values) => save.mutate(values)} onValuesChange={(values) => {
        if (values.course_group) form.setFieldValue('duration', values.course_group === 'QA' ? 35 : 45);
      }}>
        <Row gutter={20}><Col span={12}><Form.Item name="name" label={isUser ? '姓名' : '名称'} rules={[{ required: true }]}><Input maxLength={80} /></Form.Item></Col>
          {isUser ? <Col span={12}><Form.Item name="username" label="登录账号" rules={[{ required: true }, { pattern: /^[A-Za-z0-9_.-]{3,60}$/, message: '3—60 位字母、数字、点、下划线或短横线' }]}><Input /></Form.Item></Col>
            : kind === 'catalog' ? <Col span={12}><Form.Item name="course_group" label="课程分组" rules={[{ required: true }]}><Select options={groups.map((g) => ({ value: g, label: labels[g] }))} /></Form.Item></Col>
              : <Col span={12}><Form.Item name="kind" label="教室类型" rules={[{ required: true }]}><Select options={roomOptions} /></Form.Item></Col>}
        </Row>
        {isUser ? <>
          <Row gutter={20}><Col span={12}><Form.Item name="phone" label="联系方式"><Input maxLength={40} /></Form.Item></Col><Col span={12}><Form.Item name="password" label={editing?.id ? '重置密码（留空保留）' : '初始密码'} rules={[{ required: !editing?.id, message: '请设置初始密码' }, { min: 12, message: '至少 12 位' }]}><Input.Password autoComplete="new-password" /></Form.Item></Col></Row>
          {kind === 'students' ? <Row gutter={20}><Col span={8}><Form.Item name="age" label="年龄" rules={[{ required: true }]}><InputNumber min={3} max={100} /></Form.Item></Col><Col span={8}><Form.Item name="grade" label="年级" rules={[{ required: true }]}><InputNumber min={0} max={12} /></Form.Item></Col><Col span={8}><Form.Item name="gender" label="性别"><Select allowClear options={[{ label: '男', value: '男' }, { label: '女', value: '女' }]} /></Form.Item></Col></Row>
            : <Form.Item name="bio" label="教师介绍"><Input.TextArea rows={3} maxLength={4000} /></Form.Item>}
          {kind === 'students' && <Form.Item name="notes" label="跟进备注"><Input.TextArea rows={3} maxLength={2000} /></Form.Item>}
          <Form.Item name="active" label="账号启用" valuePropName="checked"><Switch /></Form.Item>
        </> : <>
          {kind === 'catalog' ? <>
            <Row gutter={20}><Col span={8}><Form.Item name="duration" label="课程时长（分钟）"><InputNumber disabled /></Form.Item></Col><Col span={8}><Form.Item name="min_size" label="最少开班人数" rules={[{ required: true }]}><InputNumber min={1} max={300} /></Form.Item></Col><Col span={8}><Form.Item name="max_size" label="最大容量" rules={[{ required: true }]}><InputNumber min={1} max={300} /></Form.Item></Col></Row>
            <Row gutter={20}>{[['min_age', '最小年龄'], ['max_age', '最大年龄'], ['min_grade', '最低年级'], ['max_grade', '最高年级']].map(([key, label]) => <Col span={6} key={key}><Form.Item name={key} label={label} rules={[{ required: true }]}><InputNumber min={key.includes('grade') ? 0 : 3} max={key.includes('grade') ? 12 : 100} /></Form.Item></Col>)}</Row>
            <Form.Item name="preferred_room" label="优先教室类型"><Select options={roomOptions} /></Form.Item><Form.Item name="description" label="课程介绍"><Input.TextArea rows={3} maxLength={4000} /></Form.Item>
          </> : <><Form.Item name="capacity" label="教室容量" rules={[{ required: true }]}><InputNumber min={1} max={300} /></Form.Item><Form.Item name="notes" label="教室备注"><Input.TextArea rows={2} maxLength={2000} /></Form.Item></>}
          <Form.Item name="features" label={kind === 'catalog' ? '必须具备的设备' : '教室设备'}><Checkbox.Group options={featureOptions} /></Form.Item>
          <Form.Item name="status" label="资源状态"><Select options={(kind === 'rooms' ? ['OPEN', 'MAINTENANCE', 'CLOSED'] : ['OPEN', 'CLOSED']).map((value) => ({ value, label: labels[value] }))} /></Form.Item>
        </>}
      </Form>
    </Modal>
    <Modal title={'授课资质 · ' + qualification?.name} open={!!qualification} onCancel={() => setQualification(null)} confirmLoading={saveQualification.isPending} onOk={() => saveQualification.mutate()} okText="保存资质">
      <Select mode="multiple" style={{ width: '100%' }} value={courseIds} onChange={setCourseIds} options={courses.map((course) => ({ value: course.id, label: course.name }))} placeholder="选择已具备授课资质的课程" />
    </Modal>
    {hoursFor && <RoomHours key={hoursFor.id} roomId={hoursFor.id} onClose={() => setHoursFor(null)} />}
  </>;
}

function RoomHours({ roomId, onClose }: { roomId: string; onClose: () => void }) {
  const cache = useQueryClient();
  const query = useQuery({ queryKey: ['room-availability', roomId], queryFn: () => get<Data>('/admin/rooms/' + roomId + '/availability'), refetchOnWindowFocus: false });
  const [selection, setSelection] = useState<string[] | null>(null);
  const chosen = selection || query.data?.availability.map((slot: Data) => slot.day_index + ':' + slot.slot_code) || [];
  const save = useMutation({ mutationFn: () => post('/admin/rooms/' + roomId + '/availability', { version: query.data!.room.version,
    slots: chosen.map((key: string) => { const [day, code] = key.split(':'); return { day_index: Number(day), slot_code: code }; }) }),
    onSuccess: () => { message.success('教室开放时段已保存'); cache.invalidateQueries(); onClose(); } });
  return <Modal title={'教室开放时段' + (query.data ? ' · ' + query.data.room.name : '')} open onCancel={onClose} onOk={() => save.mutate()} okText="保存开放时段"
    okButtonProps={{ disabled: !query.data || query.isError }} confirmLoading={save.isPending} width={1000}>
    <Alert type="info" showIcon message="按统一教学时段设置每周开放时间。已有生效课程占用的时段需先通过异常流程调整。" style={{ marginBottom: 20 }} />
    {query.isError ? <Alert type="error" message="教室开放时段读取失败" /> : <Checkbox.Group value={chosen} onChange={(values) => setSelection(values as string[])}>
      <Space direction="vertical" size={18}>{weekdays.map((day, index) => <div key={day}><strong style={{ display: 'inline-block', width: 50 }}>{day}</strong>
        {(query.data?.slots || []).map((slot: Data) => <Checkbox key={slot.code} value={index + ':' + slot.code}>{slot.code} {slot.start}</Checkbox>)}
      </div>)}</Space>
    </Checkbox.Group>}
  </Modal>;
}
