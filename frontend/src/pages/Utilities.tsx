import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Col, Form, InputNumber, Row, Table, Typography, message } from 'antd';
import dayjs from 'dayjs';
import { get, post } from '../api/client';
import { useBootstrap } from '../hooks/useBootstrap';
import { Loading, PageTitle, Status } from '../components/Common';
import type { Data } from '../types';
export function RulesPage() {
  const { data, isPending } = useQuery({ queryKey: ['rules'], queryFn: () => get('/admin/rules') });
  const [form] = Form.useForm();
  const cache = useQueryClient();
  const save = useMutation({ mutationFn: (values: Data) => post('/admin/rules', values),
    onSuccess: () => { message.success('规则已保存'); cache.invalidateQueries(); } });
  if (isPending || !data) return <Loading />;
  return <div className="section-stack"><PageTitle title="规则配置" description="可收紧工作量限制并调整求解预算，安全基线由后端强制执行" />
    <Card><Form form={form} layout="vertical" initialValues={data.rules} onFinish={(values) => save.mutate(values)}><Row gutter={24}>
      {[
        ['max_daily', '教师每日最多授课节数', 1, 5], ['max_weekly', '教师每周最多授课节数', 1, 24],
        ['max_consecutive', '最多连续授课节数', 1, 3], ['min_main_daily', '机构每日最少主课节数', 2, 50],
        ['solve_seconds', '求解预算（秒）', 1, 300],
      ].map(([name, label, min, max]) => <Col xs={24} md={8} key={String(name)}><Form.Item name={String(name)} label={String(label)} rules={[{ required: true }]}><InputNumber min={Number(min)} max={Number(max)} style={{ width: '100%' }} /></Form.Item></Col>)}
    </Row><Button type="primary" htmlType="submit" loading={save.isPending}>保存规则</Button></Form></Card>
    <Card title="统一教学时段"><Table<Data> rowKey="code" pagination={false} dataSource={data.slots} columns={[
      { title: '时段', dataIndex: 'code' }, { title: '开始', dataIndex: 'start' }, { title: '结束', dataIndex: 'end' },
      { title: '分钟', dataIndex: 'duration_minutes' }, { title: '用途', dataIndex: 'kind', render: (value) => value === 'QA' ? '答疑专用' : '常规教学' },
    ]} /><Alert type="info" style={{ marginTop: 16 }} message="教学时段采用受控配置，普通排课和调课操作无法修改大课间、午休及收尾缓冲。" /></Card>
  </div>;
}
export function RecordsPage({ type }: { type: 'audit' | 'demand' }) {
  const [page, setPage] = useState(1);
  const { data: bootstrap } = useBootstrap();
  const query = useQuery({ queryKey: ['records', type, page, bootstrap?.default_week], queryFn: () => get('/admin/' + type, {
    page, page_size: 20, ...(type === 'demand' ? { week: bootstrap?.default_week } : {}),
  }), enabled: !!bootstrap, refetchInterval: 5000 });
  const columns = type === 'audit' ? [
    { title: '时间', dataIndex: 'created_at', render: (v: string) => dayjs(v).format('YYYY-MM-DD HH:mm:ss') },
    { title: '业务操作', dataIndex: 'action' }, { title: '资源类型', dataIndex: 'entity_type' },
    { title: '资源编号', dataIndex: 'entity_id', render: (v: string) => <Typography.Text copyable={{ text: v }}>{v?.slice(0, 8)}</Typography.Text> },
    { title: '请求编号', dataIndex: 'request_id', ellipsis: true },
  ] : [
    { title: '学生', dataIndex: 'student_name' }, { title: '课程', dataIndex: 'course_name' },
    { title: '排课周', dataIndex: 'week' }, { title: '审核状态', dataIndex: 'status', render: (v: string) => <Status value={v} /> },
  ];
  return <><PageTitle title={type === 'audit' ? '审计日志' : '选课需求池'} description={type === 'audit' ? '查询业务操作与权限拒绝记录' : '按已审核需求进行真实排课，未审核需求不进入求解池'} />
    <Card><Table rowKey="id" loading={query.isPending} dataSource={query.data?.items || []} columns={columns}
      pagination={{ current: page, pageSize: 20, total: query.data?.total || 0, onChange: setPage }} /></Card></>;
}
