import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Card, Col, List, Row, Space, Statistic } from 'antd';
import { useNavigate } from 'react-router-dom';
import { get } from '../api/client';
import { useAuth } from '../stores/auth';
import { useBootstrap } from '../hooks/useBootstrap';
import { Loading, Nothing, PageTitle, Status } from '../components/Common';
import type { Schedule } from '../types';
export default function PersonalDashboard() {
  const user = useAuth((s) => s.user)!;
  const navigate = useNavigate();
  const { data: bootstrap } = useBootstrap();
  const { data, isPending, isError } = useQuery({
    queryKey: ['personal-home', user.id, bootstrap?.default_week],
    queryFn: () => get<Schedule>(user.role === 'TEACHER' ? '/teachers/me/schedule' : '/students/me/schedule', { week: bootstrap?.default_week }),
    enabled: !!bootstrap, refetchInterval: 5000,
  });
  if (isError) return <Alert type="error" message="暂时无法读取本人课表" />;
  if (isPending || !data) return <Loading />;
  const active = data.sessions.filter((s) => s.version_status === 'ACTIVE' && s.status === 'SCHEDULED');
  const today = active.filter((s) => s.day === bootstrap?.today);
  return <div className="section-stack"><PageTitle title={user.name + '，你好'} description="查看课程安排，处理本人的教学与学习事项"
    actions={<Button type="primary" onClick={() => navigate(user.role === 'STUDENT' ? '/courses' : '/reviews')}>{user.role === 'STUDENT' ? '选择课程' : '查看开课审核'}</Button>} />
    <Row gutter={[18, 18]}>{[['本周生效课程', active.length], ['今日课程', today.length], ['待确认课次', data.sessions.filter((s) => s.confirmation === 'PENDING').length]].map(([title, value]) => <Col xs={24} sm={8} key={title}><Card className="stat-card"><Statistic title={title} value={value as number} /></Card></Col>)}</Row>
    <Card title="今日安排" extra={<Button type="link" onClick={() => navigate('/schedule')}>查看本周课表</Button>}>
      {today.length ? <List dataSource={today} renderItem={(s) => <List.Item><List.Item.Meta title={s.course_name} description={s.start_time + '—' + s.end_time + ' · ' + s.room_name} /><Status value={s.status} /></List.Item>} /> : <Nothing text="今天暂无已生效课程" />}
    </Card>
    <Card title="常用操作"><Space wrap><Button onClick={() => navigate('/schedule')}>确认课程安排</Button><Button onClick={() => navigate('/changes')}>查看异常工单</Button><Button onClick={() => navigate('/notifications')}>查看通知</Button></Space></Card>
  </div>;
}
