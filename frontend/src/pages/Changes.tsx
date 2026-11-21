import { isAdminPortal } from '../portal';
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Col, DatePicker, Descriptions, Form, Input, Modal, Row, Select, Space, Table, Timeline, Typography, message } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import { get, post } from '../api/client';
import { useBootstrap } from '../hooks/useBootstrap';
import { useAuth } from '../stores/auth';
import { PageTitle, Status } from '../components/Common';
import { labels, type Data, type Schedule } from '../types';

export default function Changes({ approvals = false, requests = false }: { approvals?: boolean; requests?: boolean }) {
  const user = useAuth((s) => s.user)!;
  const administrator = isAdminPortal && user.role === 'ADMIN';
  const { data: bootstrap } = useBootstrap();
  const [open, setOpen] = useState(false);
  const [selection, setSelected] = useState<Data | null>(null);
  const [form] = Form.useForm();
  const [comment, setComment] = useState('');
  const [simulating, setSimulating] = useState<string>();
  const cache = useQueryClient();
  const query = useQuery({
    queryKey: ['changes', user.id], queryFn: () => get<Data[]>(administrator ? '/admin/change-requests' : '/change-requests/mine'),
    refetchInterval: 5000,
  });
  const detail = useQuery({ queryKey: ['change-detail', selection?.id], queryFn: () => get<Data>('/change-requests/' + selection!.id),
    enabled: !!selection, refetchInterval: 5000 });
  const selected = detail.data?.id === selection?.id ? detail.data : selection;
  const schedule = useQuery({
    queryKey: ['change-schedule', user.id, bootstrap?.default_week],
    queryFn: () => get<Schedule>(administrator ? '/admin/schedule' : user.role === 'TEACHER' ? '/teachers/me/schedule' : '/students/me/schedule', { week: bootstrap?.default_week }),
    enabled: !!bootstrap && open,
  });
  const rooms = useQuery({ queryKey: ['public-rooms'], queryFn: () => get<Data[]>('/rooms'), enabled: open });
  const demand = useQuery({ queryKey: ['approved-demand'], queryFn: () => isAdminPortal ? get<Data>('/admin/demand', { week: bootstrap?.default_week, page_size: 500 }) : Promise.resolve({ items: [] }), enabled: administrator && open });
  const active = schedule.data?.versions.find((v) => v.status === 'ACTIVE');
  const lessons = schedule.data?.sessions.filter((s) => s.version_id === active?.id && s.status === 'SCHEDULED') || [];
  const kind = Form.useWatch('kind', form);
  const sessionIds: string[] = Form.useWatch('session_ids', form) || [];
  const isLeave = ['TEACHER_LEAVE', 'STUDENT_LEAVE', 'ROOM_FAULT', 'EQUIPMENT_FAULT'].includes(kind);
  const kinds = user.role === 'STUDENT' ? ['STUDENT_LEAVE', 'RESCHEDULE', 'ENROLLMENT_CHANGE'] : user.role === 'TEACHER'
    ? ['TEACHER_LEAVE', 'RESCHEDULE', 'ROOM_FAULT', 'EQUIPMENT_FAULT', 'SUBSTITUTE']
    : ['TEACHER_LEAVE', 'RESCHEDULE', 'ROOM_FAULT', 'EQUIPMENT_FAULT', 'SUBSTITUTE', 'CANCEL_SESSION', 'ADD_SESSION'];
  const create = useMutation({ mutationFn: (values: Data) => {
    const first = lessons.find((lesson) => lesson.id === values.session_ids?.[0]);
    const payload: Data = { session_ids: values.session_ids || [] };
    if (values.range) { payload.starts_at = values.range[0].toISOString(); payload.ends_at = values.range[1].toISOString(); }
    if (values.target_day && values.target_slot) { payload.target_day = values.target_day.format('YYYY-MM-DD'); payload.target_slot = values.target_slot; }
    if (values.room_id) payload.room_id = values.room_id;
    if (['ROOM_FAULT', 'EQUIPMENT_FAULT'].includes(values.kind)) payload.owner_id = first?.room_id;
    if (values.kind === 'TEACHER_LEAVE') payload.owner_id = user.role === 'TEACHER' ? user.id : first?.teacher_id;
    if (values.kind === 'STUDENT_LEAVE') payload.owner_id = user.id;
    if (values.request_ids) payload.request_ids = values.request_ids;
    return post('/change-requests', { version_id: active!.id, kind: values.kind, reason: values.reason, payload });
  }, onSuccess: () => { message.success('申请已提交，可继续生成调整方案'); setOpen(false); cache.invalidateQueries(); } });
  const decision = useMutation({ mutationFn: (approved: boolean) => isAdminPortal ? post('/admin/change-requests/' + selected!.id + '/decision', { approved, comment, version: selected!.version }) : Promise.reject(new Error('此入口不提供审批操作')),
    onSuccess: (data) => { setSelected(data); setComment(''); cache.invalidateQueries(); message.success('审批结果已保存'); } });
  const withdraw = useMutation({ mutationFn: (change: Data) => post('/change-requests/' + change.id + '/cancel', { reason: '申请人撤回' }),
    onSuccess: () => { setSelected(null); cache.invalidateQueries(); message.success('申请已撤回'); } });
  async function simulate(change: Data) {
    setSimulating(change.id);
    try {
      const data = await post('/change-requests/' + change.id + '/simulate');
      setSelected(data); cache.invalidateQueries(); message.success('已完成真实约束求解，请查看方案');
    } finally { setSimulating(undefined); }
  }
  if (query.isError) return <Alert type="error" message="异常事务暂不可用" />;
  const visible = isAdminPortal && approvals ? query.data?.filter((c) => ['PENDING_ADMIN', 'WAITING_CONFIRMATION'].includes(c.status)) : query.data;
  return <><PageTitle title={isAdminPortal && approvals ? '审批中心' : requests ? '请假调课' : administrator ? '异常事件中心' : '异常工单'} description="申请、方案、审批和确认使用同一事务编号，全程保留真实进度"
    actions={<Button type="primary" icon={<PlusOutlined />} onClick={() => { setOpen(true); form.resetFields(); form.setFieldsValue({ kind: kinds[0], session_ids: [] }); }}>提交异常申请</Button>} />
    <Card><Table<Data> rowKey="id" loading={query.isPending} dataSource={visible || []} scroll={{ x: 1000 }} columns={[
      { title: '事务编号', dataIndex: 'id', render: (id) => <Typography.Text copyable={{ text: id }}>{id.slice(0, 8)}</Typography.Text> },
      { title: '类型', dataIndex: 'kind', render: (value) => labels[value] || value }, { title: '原因', dataIndex: 'reason', ellipsis: true },
      { title: '业务状态', dataIndex: 'status', render: (value) => <Status value={value} /> },
      { title: '方案状态', render: (_, row) => row.proposal ? <Status value={row.proposal.solver_status} /> : '待生成' },
      { title: '操作', width: 225, render: (_, row) => <Space className="table-actions"><Button type="link" onClick={() => { setSelected(row); setComment(''); }}>查看进度</Button>
        {row.status === 'PENDING_ADMIN' && <Button type="link" loading={simulating === row.id} onClick={() => simulate(row)}>生成调整方案</Button>}</Space> },
    ]} /></Card>
    <Modal title="提交请假或异常申请" width={720} open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} okText="提交申请" confirmLoading={create.isPending} okButtonProps={{ disabled: !active }}>
      {!active && <Alert type="warning" showIcon message="当前周尚无生效课表，请先完成课表发布与双方确认。" style={{ marginBottom: 20 }} />}
      <Form form={form} layout="vertical" onFinish={(values) => create.mutate(values)}>
        <Form.Item name="kind" label="申请类型" rules={[{ required: true }]}><Select options={kinds.map((key) => ({ value: key, label: labels[key] }))} /></Form.Item>
        {!isAdminPortal || kind !== 'ADD_SESSION' ? <Form.Item name="session_ids" label="受影响课次" rules={[{ required: true, message: '请选择课次' }]}>
          <Select mode="multiple" options={lessons.map((s) => ({ value: s.id, label: s.course_name + ' · ' + s.day + ' ' + s.slot_code + ' ' + s.start_time + ' · ' + s.teacher_name }))} placeholder="选择本人相关生效课次" />
        </Form.Item> : <Form.Item name="request_ids" label="待安排的真实选课需求" rules={[{ required: true }]}><Select mode="multiple" options={(demand.data?.items || []).filter((r: Data) => r.status === 'APPROVED').map((r: Data) => ({ value: r.id, label: r.course_name + ' · ' + r.student_name }))} /></Form.Item>}
        {isLeave && <Form.Item name="range" label="请假或故障时间范围" rules={[{ required: true }]}><DatePicker.RangePicker showTime format="YYYY-MM-DD HH:mm" /></Form.Item>}
        {kind === 'RESCHEDULE' && <Row gutter={20}><Col span={12}><Form.Item name="target_day" label="希望调整至日期"><DatePicker disabledDate={(value) => value.isBefore(dayjs(bootstrap?.default_week), 'day') || value.isAfter(dayjs(bootstrap?.default_week).add(4, 'day'), 'day')} /></Form.Item></Col>
          <Col span={12}><Form.Item name="target_slot" label="目标教学时段"><Select allowClear options={(bootstrap?.slots || []).filter((slot: Data) => !sessionIds.length || lessons.filter((lesson) => sessionIds.includes(lesson.id)).every((lesson) => lesson.course_group === 'QA' ? slot.kind === 'QA' : slot.kind === 'REGULAR')).map((s: Data) => ({ value: s.code, label: s.code + ' ' + s.start + '—' + s.end }))} /></Form.Item></Col></Row>}
        {kind === 'RESCHEDULE' && <Form.Item name="room_id" label="希望更换的教室（可选）"><Select allowClear options={(rooms.data || []).map((r) => ({ value: r.id, label: r.name }))} /></Form.Item>}
        <Form.Item name="reason" label="申请原因" rules={[{ required: true, min: 2, message: '请填写至少两个字的原因' }]}><Input.TextArea rows={3} maxLength={2000} /></Form.Item>
      </Form><Alert type="info" message="申请不会直接修改有效课表。新安排需管理员审批及受影响人员确认。" />
    </Modal>
    <Modal title="异常事务详情" width={900} open={!!selected} onCancel={() => setSelected(null)} footer={<Space>
      {selected && ['SUBMITTED', 'VALIDATING', 'PENDING_ADMIN', ...(administrator ? ['WAITING_CONFIRMATION'] : [])].includes(selected.status) && <Button onClick={() => withdraw.mutate(selected)}>撤回申请</Button>}
      {administrator && selected?.status === 'PENDING_ADMIN' && <><Button danger loading={decision.isPending} onClick={() => comment.trim() ? decision.mutate(false) : message.warning('请填写拒绝原因')}>拒绝申请</Button>
        <Button type="primary" loading={decision.isPending} disabled={!['OPTIMAL', 'FEASIBLE'].includes(selected?.proposal?.solver_status)} onClick={() => decision.mutate(true)}>批准并请求相关人员确认</Button></>}
      <Button onClick={() => setSelected(null)}>关闭</Button>
    </Space>}>
      {selected && <Space direction="vertical" size={20} style={{ width: '100%' }}>
        <Descriptions bordered size="small" column={2} items={[
          { key: 'id', label: '事务编号', children: <Typography.Text copyable>{selected.id}</Typography.Text> },
          { key: 'state', label: '当前状态', children: <Status value={selected.status} /> },
          { key: 'type', label: '异常类型', children: labels[selected.kind] },
          { key: 'reason', label: '申请原因', children: selected.reason },
          { key: 'admin', label: '审批意见', children: selected.admin_comment || '—' },
        ]} />
        {selected.proposal && <><Alert type={['OPTIMAL', 'FEASIBLE'].includes(selected.proposal.solver_status) ? 'success' : 'warning'} showIcon
          message={<Space><Status value={selected.proposal.solver_status} />{selected.proposal.strategy}</Space>} description={selected.proposal.diagnostics?.join('；') || '方案仍须完成审批和确认'} />
          <Table<Data> rowKey="id" size="small" pagination={{ pageSize: 8 }} dataSource={(selected.proposal.lessons || []).filter((s: Data) => selected.payload.session_ids?.includes(s.id) || selected.kind === 'ADD_SESSION')} columns={[
            { title: '课程', render: (_, row) => row.course_name || '课程课次' }, { title: '日期', dataIndex: 'day' },
            { title: '教学时间', render: (_, row) => row.slot_code + ' ' + row.start + '—' + row.end },
            { title: '教师', render: (_, row) => row.teacher_name || '已匹配教师' }, { title: '教室', render: (_, row) => row.room_name || '已匹配教室' },
            { title: '状态', dataIndex: 'status', render: (value) => <Status value={value} /> },
          ]} /></>}
        <Timeline items={(selected.events || []).map((event: Data) => ({ color: event.to_state === 'APPLIED' ? 'green' : 'blue',
          children: <><strong>{labels[event.to_state] || event.to_state}</strong><div className="muted">{dayjs(event.created_at).format('MM-DD HH:mm:ss')} · {event.message}</div></> }))} />
        {administrator && selected.status === 'PENDING_ADMIN' && <Input.TextArea value={comment} onChange={(e) => setComment(e.target.value)} rows={2} maxLength={2000} placeholder="填写审批意见（拒绝时必填）" />}
      </Space>}
    </Modal>
  </>;
}
