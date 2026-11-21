import { useEffect, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Card, Col, Row, Space, Statistic, Tag } from 'antd';
import { ArrowRightOutlined, CalendarOutlined, CheckCircleOutlined, TeamOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import * as echarts from 'echarts';
import { get } from '../api/client';
import { useBootstrap } from '../hooks/useBootstrap';
import { Loading, PageTitle, Status } from '../components/Common';
import type { Data } from '../types';

export function Chart({ option }: { option: echarts.EChartsOption }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    chart.setOption(option);
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(ref.current);
    return () => { observer.disconnect(); chart.dispose(); };
  }, [option]);
  return <div ref={ref} className="chart-box" />;
}
export default function Dashboard({ analytics = false }: { analytics?: boolean }) {
  const navigate = useNavigate();
  const { data: bootstrap } = useBootstrap();
  const { data, isPending, isError } = useQuery({
    queryKey: ['analytics', bootstrap?.default_week],
    queryFn: () => get('/admin/analytics', { week: bootstrap?.default_week }),
    enabled: !!bootstrap, refetchInterval: 5000,
  });
  if (isError) return <Alert type="error" showIcon message="暂时无法读取运营数据" />;
  if (isPending || !data) return <Loading />;
  const metrics = data.report?.metrics || {};
  const loads = data.teachers.map((t: Data) => metrics.teacher_load?.[t.id] || 0);
  const heat = Array.from({ length: 40 }, (_, i) => {
    const day = Math.floor(i / 8), slot = 'S' + (i % 8 + 1);
    return [day, i % 8, data.sessions.filter((s: Data) => new Date(s.day + 'T12:00:00').getDay() === day + 1 && s.slot_code === slot).length];
  });
  return <div className="section-stack">
    <PageTitle title={analytics ? '运营数据分析' : '总览仪表盘'} description={'教学运营概览 · ' + bootstrap?.default_week + ' 周'}
      actions={<Button type="primary" icon={<ThunderboltOutlined />} onClick={() => navigate('/scheduling')}>进入智能排课</Button>} />
    <Row gutter={[18, 18]}>{[
      ['在读学生', data.student_count, '机构内启用的学生账号', <TeamOutlined />],
      ['授课教师', data.teacher_count, '资质、可用时间统一管理', <CheckCircleOutlined />],
      ['教学教室', data.room_count, '容量与设备匹配', <CalendarOutlined />],
      ['本周选课需求', data.enrollment_count, '已提交至数据库', <ThunderboltOutlined />],
    ].map(([title, value, note, icon]) => <Col xs={24} sm={12} xl={6} key={String(title)}><Card className="stat-card"><Statistic title={String(title)} value={value as number} prefix={icon} /><div className="stat-note">{note}</div></Card></Col>)}</Row>
    <Row gutter={[18, 18]}><Col xs={24} xl={16}><Card title="教师周授课负载" extra={<Tag>上限 24 节 / 周</Tag>}>
      <Chart option={{ tooltip: { trigger: 'axis' }, grid: { left: 35, right: 20, bottom: 65, top: 25 },
        xAxis: { type: 'category', data: data.teachers.map((t: Data) => t.name), axisLabel: { rotate: 35, fontSize: 10 } },
        yAxis: { type: 'value', minInterval: 1 }, series: [{ type: 'bar', data: loads, barMaxWidth: 24, itemStyle: { color: '#4e7bb9', borderRadius: [4, 4, 0, 0] } }],
      }} /></Card></Col><Col xs={24} xl={8}><Card title="待办与课表质量"><Space direction="vertical" size={22} style={{ width: '100%' }}>
      <Statistic title="待教师审核" value={data.pending_reviews} /><Statistic title="待处理异常事务" value={data.pending_changes} />
      <div>当前方案 <Status value={data.version_status} /></div><div>需求满足率 <strong>{data.report ? ((metrics.satisfaction_rate || 0) * 100).toFixed(1) + '%' : '尚无方案'}</strong></div>
      <div>已校验硬约束冲突 <strong>{data.report ? data.report.hard_conflicts : '—'}</strong></div>
      <Button block onClick={() => navigate('/approvals')}>查看审批事项 <ArrowRightOutlined /></Button>
    </Space></Card></Col></Row>
    <Card title="教学时段资源占用" extra={<span className="muted">以当前所示课表版本统计</span>}>
      <Chart option={{ tooltip: { position: 'top' }, grid: { left: 45, right: 40, top: 15, bottom: 65 },
        xAxis: { type: 'category', data: ['周一', '周二', '周三', '周四', '周五'], splitArea: { show: true } },
        yAxis: { type: 'category', data: Array.from({ length: 8 }, (_, i) => 'S' + (i + 1)), inverse: true },
        visualMap: { min: 0, max: Math.max(1, ...heat.map((h) => h[2])), calculable: true, orient: 'horizontal', left: 'center', bottom: 0, inRange: { color: ['#f1f5fb', '#255c9c'] } },
        series: [{ type: 'heatmap', data: heat, label: { show: true }, itemStyle: { borderWidth: 3, borderColor: '#fff' } }],
      }} />
    </Card>
  </div>;
}
