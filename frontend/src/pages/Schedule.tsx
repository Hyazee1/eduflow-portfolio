import { isAdminPortal } from '../portal';
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, DatePicker, Grid, Input, Modal, Select, Space, Table, Tag, message } from 'antd';
import dayjs from 'dayjs';
import { get, post } from '../api/client';
import { useBootstrap } from '../hooks/useBootstrap';
import { useAuth } from '../stores/auth';
import { Loading, Nothing, PageTitle, Status } from '../components/Common';
import { WeekGrid } from '../components/WeekGrid';
import { groups, labels, type Lesson, type Schedule as ScheduleData } from '../types';

export default function Schedule() {
  const user = useAuth((s) => s.user)!;
  const administrator = isAdminPortal && user.role === 'ADMIN';
  const { data: bootstrap } = useBootstrap();
  const [week, setWeek] = useState<string>();
  const [versionId, setVersionId] = useState<string>();
  const [group, setGroup] = useState<string>();
  const [teacher, setTeacher] = useState<string>();
  const [room, setRoom] = useState<string>();
  const screens = Grid.useBreakpoint();
  const [view, setView] = useState<'day' | 'week'>();
  const [chosenDay, setChosenDay] = useState<string>();
  const dayOnly = view ? view === 'day' : !screens.md;
  const [rejectOpen, setRejectOpen] = useState(false);
  const [reason, setReason] = useState('');
  const [pausing, setPausing] = useState<Lesson | null>(null);
  const [pauseReason, setPauseReason] = useState('');
  const cache = useQueryClient();
  const currentWeek = week || bootstrap?.default_week;
  const url = administrator ? '/admin/schedule' : user.role === 'TEACHER' ? '/teachers/me/schedule' : '/students/me/schedule';
  const { data, isPending, isError } = useQuery({
    queryKey: ['schedule', user.id, currentWeek], queryFn: () => get<ScheduleData>(url, { week: currentWeek }),
    enabled: !!currentWeek, refetchInterval: 5000,
  });
  const selected = data?.versions.find((v) => v.id === versionId) || data?.versions.find((v) => v.status === 'PUBLISHED_PENDING_CONFIRMATION')
    || data?.versions.find((v) => v.status === 'ACTIVE') || data?.versions[data.versions.length - 1];
  const sessions = data?.sessions.filter((s) => s.version_id === selected?.id) || [];
  const awaiting = sessions.filter((s) => s.confirmation === 'PENDING');
  const confirm = useMutation({ mutationFn: (decision: string) => post('/schedule-confirmations', {
    version_id: selected!.id, session_ids: awaiting.map((s) => s.id), decision, reason,
  }), onSuccess: () => { message.success('确认决定已保存'); setRejectOpen(false); setReason(''); cache.invalidateQueries(); } });
  const pause = useMutation({ mutationFn: () => isAdminPortal ? post('/admin/sessions/' + pausing!.record_id + '/pause', { reason: pauseReason }) : Promise.reject(new Error('此入口不提供停课操作')),
    onSuccess: () => { message.success('课次已暂停，师生通知与异常记录已创建'); setPausing(null); setPauseReason(''); cache.invalidateQueries(); } });
  function openPause(lesson: Lesson) { setPausing(lesson); setPauseReason(''); }
  if (isError) return <Alert type="error" message="无法读取课表，请检查服务状态" />;
  if (isPending || !data || !bootstrap) return <Loading />;
  const slots = data.version_slots?.[selected?.id || ''] || data.slots;
  const filtered = sessions.filter((s) => (!group || s.course_group === group) && (!teacher || s.teacher_id === teacher) && (!room || s.room_id === room));
  const dates = Array.from({ length: 5 }, (_, i) => ({ value: dayjs(currentWeek).add(i, 'day').format('YYYY-MM-DD'), label: '周' + '一二三四五'[i] + ' ' + dayjs(currentWeek).add(i, 'day').format('MM-DD') }));
  const displayedDay = dates.some((d) => d.value === chosenDay) ? chosenDay : dates.some((d) => d.value === bootstrap.today) ? bootstrap.today : dates[0].value;
  const daily = filtered.filter((s) => s.day === displayedDay);
  const teacherOptions = [...new Map(sessions.map((s) => [s.teacher_id, { value: s.teacher_id, label: s.teacher_name }])).values()];
  const roomOptions = [...new Map(sessions.map((s) => [s.room_id, { value: s.room_id, label: s.room_name }])).values()];
  return <div className="section-stack"><PageTitle title={administrator ? '全机构课表' : '本周课表'} description="统一时段 · 周一至周五 · S8 答疑专用"
    actions={<><DatePicker picker="week" value={dayjs(currentWeek)} allowClear={false} onChange={(value) => { setWeek(value.startOf('isoWeek').format('YYYY-MM-DD')); setVersionId(undefined); }} /><Button onClick={() => setView(dayOnly ? 'week' : 'day')}>{dayOnly ? '周视图' : '日视图'}</Button></>} />
    <Card><div className="toolbar"><Select style={{ minWidth: 280 }} placeholder="暂无课表版本" value={selected?.id} onChange={setVersionId} options={data.versions.map((v) => ({ value: v.id, label: labels[v.status] + ' · ' + v.id.slice(0, 8) }))} />
      {selected && <Status value={selected.status} />}
      {dayOnly && <Select aria-label="查看日期" value={displayedDay} options={dates} onChange={setChosenDay} style={{ minWidth: 155 }} />}
      {administrator && <><Select allowClear placeholder="课程分组" style={{ width: 130 }} value={group} onChange={setGroup} options={groups.map((key) => ({ value: key, label: labels[key] }))} />
        <Select allowClear placeholder="全部教师" style={{ width: 145 }} value={teacher} onChange={setTeacher} options={teacherOptions} /><Select allowClear placeholder="全部教室" style={{ width: 170 }} value={room} onChange={setRoom} options={roomOptions} /></>}
    </div>
      {selected?.status === 'PUBLISHED_PENDING_CONFIRMATION' && <Alert style={{ marginBottom: 18 }} type="warning" showIcon message="此版本尚未生效，等待师生确认及最终资源复检。"
        action={!administrator && awaiting.length ? <Space><Button type="primary" size="small" loading={confirm.isPending} onClick={() => confirm.mutate('ACCEPTED')}>确认本人全部课次</Button><Button size="small" onClick={() => setRejectOpen(true)}>拒绝并说明原因</Button></Space> : null} />}
      {!selected ? <Nothing text="尚无课表，请等待管理员完成排课" /> : dayOnly && !screens.md ? <div className="section-stack">{daily.length ? daily.map((lesson) => <Card key={lesson.id} size="small" title={lesson.course_name} extra={<Status value={lesson.status} />}><p>{lesson.slot_code} · {lesson.start_time}—{lesson.end_time}</p><p>{lesson.teacher_name} · {lesson.room_name}</p><Tag>{labels[lesson.course_group]}</Tag>{administrator && lesson.version_status === 'ACTIVE' && lesson.status === 'SCHEDULED' && <Button danger onClick={() => openPause(lesson)}>紧急停课</Button>}</Card>) : <Nothing text="当日没有课程安排" />}</div> : dayOnly ? <Table rowKey={(s) => s.version_id + s.id} dataSource={daily} columns={[
        { title: '课程', dataIndex: 'course_name' }, { title: '时段', render: (_, s) => s.slot_code + ' ' + s.start_time + '—' + s.end_time },
        { title: '教师', dataIndex: 'teacher_name' }, { title: '教室', dataIndex: 'room_name' },
        { title: '状态', render: (_, s) => <Status value={s.status} /> },
        ...(administrator ? [{ title: '操作', render: (_: unknown, lesson: Lesson) => lesson.version_status === 'ACTIVE' && lesson.status === 'SCHEDULED'
          ? <Button type="link" danger onClick={() => openPause(lesson)}>紧急停课</Button> : null }] : []),
      ]} /> : <WeekGrid week={currentWeek!} slots={slots} breaks={data.breaks} lessons={filtered} onPause={administrator ? openPause : undefined} />}
    </Card>
    {user.role === 'TEACHER' && <Card title="本人每日授课与休息"><Space wrap>{Array.from({ length: 5 }, (_, i) => {
      const day = dayjs(currentWeek).add(i, 'day').format('YYYY-MM-DD');
      const daily = sessions.filter((s) => s.day === day && s.teacher_id === user.id && s.status === 'SCHEDULED');
      return <Tag key={day}>周{'一二三四五'[i]}：{daily.length} 节 · {daily.reduce((sum, s) => sum + (slots.find((slot) => slot.code === s.slot_code)?.duration_minutes || 0), 0)} 分钟 · 上午空闲 {slots.filter((slot) => slot.period === 'AM').length - daily.filter((s) => slots.find((slot) => slot.code === s.slot_code)?.period === 'AM').length} 节 · 下午空闲 {slots.filter((slot) => slot.period === 'PM').length - daily.filter((s) => slots.find((slot) => slot.code === s.slot_code)?.period === 'PM').length} 节</Tag>;
    })}</Space></Card>}
    <Modal title="拒绝课表确认" open={rejectOpen} onCancel={() => setRejectOpen(false)} okText="提交拒绝原因" confirmLoading={confirm.isPending} onOk={() => reason.trim() ? confirm.mutate('REJECTED') : message.warning('请填写原因')}>
      <p>系统将创建异常事务，并阻止此版本生效。</p><Input.TextArea rows={4} maxLength={2000} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="请说明不接受安排的原因" />
    </Modal>
    {isAdminPortal && <Modal title="紧急停课" open={!!pausing} onCancel={() => setPausing(null)} okText="暂停并通知师生" okButtonProps={{ danger: true }} confirmLoading={pause.isPending}
      onOk={() => pauseReason.trim().length >= 2 ? pause.mutate() : message.warning('请填写至少两个字的停课原因')}>
      <p>{pausing?.course_name} · {pausing?.day} {pausing?.slot_code} · {pausing?.teacher_name}</p>
      <Alert type="warning" showIcon message="停课立即生效并释放资源预订，系统将通知相关师生。后续新安排仍需审批和确认。" style={{ marginBottom: 16 }} />
      <Input.TextArea rows={3} maxLength={2000} value={pauseReason} onChange={(event) => setPauseReason(event.target.value)} placeholder="说明紧急停课原因" />
    </Modal>}
  </div>;
}
