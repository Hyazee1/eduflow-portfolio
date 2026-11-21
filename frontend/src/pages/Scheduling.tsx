import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Col, DatePicker, Descriptions, Form, Modal, Row, Select, Space, Statistic, Table, Tabs, Tooltip, message } from 'antd';
import { CheckCircleOutlined, SendOutlined, ThunderboltOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import { useSearchParams } from 'react-router-dom';
import { get, post } from '../api/client';
import { useBootstrap } from '../hooks/useBootstrap';
import { Loading, Nothing, PageTitle, Status } from '../components/Common';
import { WeekGrid } from '../components/WeekGrid';
import { labels, type Data, type Lesson, type Schedule } from '../types';

export default function Scheduling() {
  const { data: bootstrap } = useBootstrap();
  const [params, setParams] = useSearchParams();
  const [week, setWeek] = useState<string>();
  const currentWeek = week || params.get('week') || bootstrap?.default_week;
  const [jobId, setJobId] = useState<string>();
  const [profile, setProfile] = useState('balanced');
  const [selectedId, setSelectedId] = useState<string>();
  const [editing, setEditing] = useState<Lesson | null>(null);
  const [copyOpen, setCopyOpen] = useState(false);
  const [targetWeek, setTargetWeek] = useState<dayjs.Dayjs | null>(null);
  const [form] = Form.useForm();
  const cache = useQueryClient();
  const versions = useQuery({
    queryKey: ['versions', currentWeek], queryFn: () => get('/admin/versions', { week: currentWeek, page_size: 100 }),
    enabled: !!currentWeek, refetchInterval: 5000,
  });
  const versionId = selectedId || params.get('version') || versions.data?.items[0]?.id;
  const version = useQuery({
    queryKey: ['version', versionId], queryFn: () => get('/admin/schedule/versions/' + versionId),
    enabled: !!versionId, refetchInterval: 5000,
  });
  const schedule = useQuery({
    queryKey: ['candidate-schedule', currentWeek, versionId],
    queryFn: () => get<Schedule>('/admin/schedule', { week: currentWeek, version_id: versionId }),
    enabled: !!versionId && !!currentWeek, refetchInterval: 5000,
  });
  const job = useQuery({
    queryKey: ['job', jobId], queryFn: () => get('/admin/schedule/jobs/' + jobId), enabled: !!jobId,
    refetchInterval: (query) => ['QUEUED', 'RUNNING'].includes(query.state.data?.status || '') ? 2000 : false,
  });
  const teachers = useQuery({ queryKey: ['teacher-options'], queryFn: () => get('/admin/teachers', { page_size: 500 }) });
  const rooms = useQuery({ queryKey: ['room-options'], queryFn: () => get('/admin/rooms', { page_size: 500 }) });
  useEffect(() => {
    if (job.data?.version_id) { setSelectedId(job.data.version_id); cache.invalidateQueries({ queryKey: ['versions'] }); }
  }, [job.data?.version_id, cache]);
  const expired = versions.data?.items.find((v: Data) => v.status === 'PUBLISHED_PENDING_CONFIRMATION' && dayjs(v.confirmation_deadline).isBefore(dayjs()));
  const solve = useMutation({ mutationFn: () => post('/admin/schedule/solve', { week: currentWeek, optimization_profile: profile,
    reference_version_id: profile === 'preference_first' ? (expired?.id || versionId) : undefined }),
    onSuccess: (data) => { setJobId(data.id); message.success('后台排课任务已排队'); cache.invalidateQueries(); } });
  const validate = useMutation({ mutationFn: () => post('/admin/schedule/versions/' + versionId + '/validate', { version: version.data!.version }),
    onSuccess: () => { message.success('独立规则校验通过'); cache.invalidateQueries(); } });
  const publish = useMutation({ mutationFn: async () => {
    let data = version.data!;
    if (data.status === 'DRAFT') data = await post('/admin/schedule/versions/' + versionId + '/validate', { version: data.version });
    return post('/admin/schedule/versions/' + versionId + '/publish', { version: data.version,
      ...(expired && expired.id !== versionId ? { replace_expired_version_id: expired.id, replace_expired_version_revision: expired.version } : {}) });
  }, onSuccess: () => { message.success('已发布，等待相关师生确认'); cache.invalidateQueries(); } });
  const edit = useMutation({ mutationFn: (values: Data) => post('/admin/schedule/versions/' + versionId + '/sessions', {
    ...values, day: values.day.format('YYYY-MM-DD'), version: version.data!.version, session_id: editing!.id,
  }), onSuccess: (data) => { setEditing(null); setSelectedId(data.id); setParams({ week: currentWeek!, version: data.id }); cache.invalidateQueries(); message.success('已创建新的调整草稿'); } });
  const cancel = useMutation({ mutationFn: () => post('/admin/schedule/versions/' + versionId + '/cancel', { version: version.data!.version }),
    onSuccess: () => { message.success('候选版本已取消'); cache.invalidateQueries(); } });
  const remove = useMutation({ mutationFn: () => post('/admin/schedule/versions/' + versionId + '/delete', { version: version.data!.version }),
    onSuccess: (deleted) => {
      cache.setQueryData(['versions', currentWeek], (old: Data | undefined) => old ? { ...old, items: old.items.filter((v: Data) => v.id !== deleted.id), total: Math.max(0, old.total - 1) } : old);
      setSelectedId(undefined); setJobId(undefined); setParams({ week: currentWeek! });
      cache.removeQueries({ queryKey: ['version', deleted.id] });
      cache.removeQueries({ queryKey: ['candidate-schedule', currentWeek, deleted.id] });
      cache.invalidateQueries(); message.success('草稿及候选课次已删除，选课需求保留');
    } });
  useEffect(() => {
    if (version.isError && versions.data && !versions.data.items.some((v: Data) => v.id === versionId)) {
      setSelectedId(undefined); setJobId(undefined); setParams(currentWeek ? { week: currentWeek } : {});
    }
  }, [version.isError, versions.data, versionId, currentWeek, setParams]);
  const copy = useMutation({ mutationFn: () => post('/admin/schedule/copy-week', { source_week: currentWeek, target_week: targetWeek!.startOf('isoWeek').format('YYYY-MM-DD') }),
    onSuccess: (data) => { message.success('已复制 ' + data.request_count + ' 条需求，等待重新审核'); setCopyOpen(false); cache.invalidateQueries(); } });
  if (!bootstrap) return <Loading />;
  const report = version.data?.report || {};
  const metrics = report.metrics || {};
  const editable = ['DRAFT', 'VALIDATED'].includes(version.data?.status);
  const busy = solve.isPending || ['QUEUED', 'RUNNING'].includes(job.data?.status || '');
  const confirmations = version.data?.confirmations || [];
  return <div className="section-stack">
    <PageTitle title="智能排课工作台" description="汇总已审核需求，校验全部硬约束，发布后等待双方确认"
      actions={<><DatePicker picker="week" allowClear={false} value={dayjs(currentWeek)} onChange={(value) => { setWeek(value.startOf('isoWeek').format('YYYY-MM-DD')); setSelectedId(undefined); setParams({}); }} />
        <Select aria-label="排课优化策略" value={profile} onChange={setProfile} style={{ minWidth: 150 }} options={[{ value: 'balanced', label: '综合均衡（60秒）' }, { value: 'preference_first', label: '首选优先（300秒）' }]} />
        <Button onClick={() => { setCopyOpen(true); setTargetWeek(dayjs(currentWeek).add(1, 'week')); }}>复制下一周需求</Button>
        <Button type="primary" icon={<ThunderboltOutlined />} loading={busy} onClick={() => solve.mutate()}>自动排课</Button></>} />
    {job.data && <Alert showIcon type={['FAILED', 'STALE'].includes(job.data.status) || ['INFEASIBLE', 'UNKNOWN', 'MODEL_INVALID'].includes(job.data.result?.solver_status) ? 'warning' : 'info'}
      message={<Space>当前任务 <Status value={job.data.status} />{job.data.result && <Status value={job.data.result.solver_status} />}</Space>}
      description={job.data.status === 'QUEUED' ? '任务保存在数据库中，等待任务服务处理。' : job.data.result?.diagnostics?.join('；') || job.data.error || '按统一时间配置与资源规则求解。'} />}
    <Card><div className="toolbar"><Select style={{ minWidth: 350 }} value={versionId} placeholder="选择候选课表版本" onChange={setSelectedId} loading={versions.isPending}
      options={(versions.data?.items || []).map((v: Data) => ({ value: v.id, label: labels[v.status] + ' · ' + v.id.slice(0, 8) + ' · ' + dayjs(v.created_at).format('MM-DD HH:mm') }))} />
      {version.data && <Status value={version.data.status} />}
      {editable && <><Button icon={<CheckCircleOutlined />} disabled={version.data?.requires_resolve} loading={validate.isPending} onClick={() => validate.mutate()}>独立校验</Button>
        <Button type="primary" icon={<SendOutlined />} disabled={version.data?.requires_resolve} loading={publish.isPending} onClick={() => Modal.confirm({ title: '发布此版本并请求师生确认？', content: expired && expired.id !== versionId ? '将原子替换同周逾期版本，保留旧版历史并重新开始24小时确认。收齐必要确认后整周生效。' : '收齐必要确认并通过最终资源复检后，整周课表才会正式生效。', okText: '发布并通知', cancelText: '返回检查', onOk: () => publish.mutateAsync() })}>校验并发布</Button></>}
      {version.data && !version.data.published_at && !version.data.activated_at && ['DRAFT', 'VALIDATED', 'CANCELLED'].includes(version.data.status) && <Tooltip title={version.data.delete_block_reason || '删除草稿及候选课次，保留选课需求'}><Button danger disabled={!version.data.can_delete} loading={remove.isPending} onClick={() => Modal.confirm({ title: '删除此草稿课表？', content: '候选课次和成员关联将永久删除。选课需求、开课审核与已有生效课表会保留。', okText: '删除草稿', okButtonProps: { danger: true }, cancelText: '保留', onOk: () => remove.mutateAsync() })}>删除草稿</Button></Tooltip>}
      {version.data?.status === 'PUBLISHED_PENDING_CONFIRMATION' && <Button danger loading={cancel.isPending} onClick={() => cancel.mutate()}>取消此候选版本</Button>}
    </div>
    {version.isError || schedule.isError ? <Alert type="error" message="版本数据读取失败" /> : !versionId ? <Nothing text="点击自动排课，生成第一份合法候选方案" /> : version.isPending ? <Loading /> : <>
      {version.data?.requires_resolve && <Alert type="warning" showIcon message="此候选使用旧排课规则，请重新自动排课或删除该草稿。" style={{ marginBottom: 16 }} />}
      <Row gutter={[18, 18]} style={{ marginBottom: 24 }}><Col xs={12} md={8} lg={4}><Statistic title="候选课次" value={metrics.session_count || 0} /></Col><Col xs={12} md={8} lg={4}><Statistic title="需求满足率" value={(metrics.satisfaction_rate || 0) * 100} precision={1} suffix="%" /></Col><Col xs={12} md={8} lg={4}><Statistic title="时段覆盖" value={metrics.covered_slots ?? '—'} suffix="/ 40" /></Col><Col xs={12} md={8} lg={4}><Statistic title="硬约束冲突" value={report.hard_conflicts ?? '—'} valueStyle={{ color: report.hard_conflicts ? '#c64f48' : '#469175' }} /></Col><Col xs={12} md={8} lg={4}><Statistic title="待审核需求" value={version.data?.pending_request_count || 0} /></Col><Col xs={12} md={8} lg={4}><Statistic title="待确认人数" value={new Set(confirmations.filter((c: Data) => c.decision === 'PENDING').map((c: Data) => c.user_id)).size} /></Col></Row>
      <Tabs items={[
        { key: 'calendar', label: '候选周课表', children: schedule.data ? <WeekGrid week={currentWeek!} slots={schedule.data.slots} breaks={schedule.data.breaks} lessons={schedule.data.sessions} onEdit={editable ? (lesson) => {
          setEditing(lesson); form.setFieldsValue({ teacher_id: lesson.teacher_id, room_id: lesson.room_id, day: dayjs(lesson.day), slot_code: lesson.slot_code });
        } : undefined} /> : <Loading /> },
        { key: 'quality', label: '求解与质量报告', children: <Space direction="vertical" size={20} style={{ width: '100%' }}>
          <Descriptions bordered size="small" column={2} items={[
            { key: 'status', label: '求解状态', children: <Status value={version.data?.solver_status} /> },
            { key: 'time', label: '实际耗时', children: (report.wall_seconds ?? '—') + ' 秒' },
            { key: 'fulfilled', label: '满足的选课需求', children: (metrics.fulfilled_count || 0) + ' / ' + (metrics.request_count || 0) },
            { key: 'preferences', label: '首选 / 任一偏好命中', children: `${metrics.first_preferred_count ?? '—'} / ${metrics.preferred_count ?? '—'} 条` },
            { key: 'coverage', label: '覆盖与教师参与', children: `${metrics.covered_slots ?? '—'}/40 时段 · ${metrics.active_teacher_count ?? '—'}/${metrics.teacher_count ?? '—'} 位教师` },
            { key: 'rest', label: '相邻两日最少休息', children: (metrics.minimum_adjacent_day_rest ?? '—') + ' 节；每日至少3节，尽量4节以上' },
            { key: 'utilization', label: '课次座位利用率', children: ((metrics.seat_utilization || 0) * 100).toFixed(1) + '%' },
          ]} /><Alert type="info" message={report.diagnostics?.join('；') || '已使用独立业务校验器检查教师、教室、学生、教学时间与工作负载。'} />
          {report.optimization?.stages && <Table<Data> rowKey="stage" size="small" pagination={false} dataSource={report.optimization.stages} columns={[
            { title: '优化阶段', dataIndex: 'stage', render: (v: string) => ({ feasibility: '完整需求可行性', served: '需求满足', first: '首选时段', any: '任一偏好', quality: '资源质量' }[v] || v) },
            { title: '真实求解状态', dataIndex: 'status', render: (v: string) => <Status value={v} /> }, { title: '耗时（秒）', dataIndex: 'seconds' },
            { title: '采用结果', dataIndex: 'accepted', render: (v: boolean) => v ? '是' : '保留已知可行结果' },
          ]} />}
          {(report.violations || []).length > 0 && <Table<Data> rowKey={(r, i) => r.code + i} dataSource={report.violations} columns={[{ title: '规则', dataIndex: 'code' }, { title: '诊断', dataIndex: 'message' }]} />}
          <Table rowKey="id" size="small" dataSource={version.data?.unmet_details || []} locale={{ emptyText: '本方案没有未满足的已审核选课需求' }} columns={[
            { title: '学生', dataIndex: 'student_name' }, { title: '课程', dataIndex: 'course_name' }, { title: '未满足原因', dataIndex: 'reason' },
          ]} />
        </Space> },
        { key: 'rest', label: '教师负载与休息', children: <Table<Data> rowKey={(r) => r.teacher_id + r.day} scroll={{ x: 650 }} dataSource={metrics.teacher_daily_rest || []} columns={[
          { title: '教师', dataIndex: 'teacher_id', render: (id: string) => teachers.data?.items.find((t: Data) => (t.user_id || t.id) === id)?.name || id.slice(0, 8) },
          { title: '日期', dataIndex: 'day' }, { title: '授课节数', dataIndex: 'lessons' }, { title: '未授课时段', dataIndex: 'rest_slots', render: (n: number) => `${n} 节${n < 3 ? '（不合规）' : ''}` },
        ]} /> },
        { key: 'confirmations', label: '双向确认进度', children: <Table rowKey="id" dataSource={confirmations} columns={[
          { title: '人员', dataIndex: 'user_name' }, { title: '身份', dataIndex: 'role', render: (v: string) => labels[v] },
          { title: '课次编号', dataIndex: 'logical_id', render: (v: string) => v?.slice(0, 8) },
          { title: '确认状态', dataIndex: 'decision', render: (v: string) => <Status value={v} /> },
          { title: '意见', dataIndex: 'reason' },
        ]} /> },
      ]} />
    </>}
    </Card>
    <Modal title="调整候选课次" width={600} open={!!editing} onCancel={() => setEditing(null)} onOk={() => form.submit()} confirmLoading={edit.isPending} okText="校验并生成调整草稿">
      <Alert type="info" showIcon message="修改会生成新草稿，并重新检查资源冲突和教师工作量。" style={{ marginBottom: 20 }} />
      <Form form={form} layout="vertical" onFinish={(values) => edit.mutate(values)}>
        <Form.Item name="teacher_id" label="授课教师" rules={[{ required: true }]}><Select options={(teachers.data?.items || []).map((t: Data) => ({ value: t.user_id, label: t.name }))} /></Form.Item>
        <Form.Item name="room_id" label="教室" rules={[{ required: true }]}><Select options={(rooms.data?.items || []).map((r: Data) => ({ value: r.id, label: r.name }))} /></Form.Item>
        <Row gutter={20}><Col span={12}><Form.Item name="day" label="上课日期" rules={[{ required: true }]}><DatePicker disabledDate={(value) => value.isBefore(dayjs(currentWeek), 'day') || value.isAfter(dayjs(currentWeek).add(4, 'day'), 'day')} /></Form.Item></Col>
          <Col span={12}><Form.Item name="slot_code" label="教学时段" rules={[{ required: true }]}><Select options={bootstrap.slots.filter((s: Data) => editing?.course_group === 'QA' ? s.kind === 'QA' : s.kind === 'REGULAR').map((s: Data) => ({ value: s.code, label: s.code + ' ' + s.start + '—' + s.end }))} /></Form.Item></Col></Row>
      </Form>
    </Modal>
    <Modal title="复制下一周选课需求" open={copyOpen} onCancel={() => setCopyOpen(false)} confirmLoading={copy.isPending} okText="复制并请求重新审核" onOk={() => targetWeek && copy.mutate()}>
      <p>复制实际选课需求及时间偏好。新一周需要重新审核、求解、发布和确认。</p><DatePicker picker="week" allowClear={false} value={targetWeek} onChange={setTargetWeek} />
    </Modal>
  </div>;
}
