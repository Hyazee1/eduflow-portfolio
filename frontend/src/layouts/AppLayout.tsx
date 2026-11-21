import { isAdminPortal } from '../portal';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Avatar, Badge, Button, Drawer, Dropdown, Grid, Layout, Menu, Space, Tag } from 'antd';
import { useState } from 'react';
import {
  ApartmentOutlined, AuditOutlined, BellOutlined, BookOutlined, CalendarOutlined, DashboardOutlined,
  ExperimentOutlined, FileDoneOutlined, FormOutlined, LogoutOutlined, SettingOutlined, TeamOutlined,
  ThunderboltOutlined, UserOutlined, WarningOutlined, MenuOutlined, LockOutlined,
} from '@ant-design/icons';
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { authClient, get } from '../api/client';
import { useAuth } from '../stores/auth';
import { useBootstrap } from '../hooks/useBootstrap';
import { labels, type Data } from '../types';
import AgentDrawer from '../components/AgentDrawer';

export default function AppLayout() {
  const user = useAuth((s) => s.user)!;
  const screens = Grid.useBreakpoint();
  const mobile = !screens.md;
  const [navigation, setNavigation] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();
  const cache = useQueryClient();
  const { data: bootstrap } = useBootstrap();
  const { data: notifications = [] } = useQuery({
    queryKey: ['notifications', user.id], queryFn: () => get<Data[]>('/notifications'), refetchInterval: 5000,
  });
  const common = [
    { key: '/', icon: <DashboardOutlined />, label: '我的工作台' },
    { key: '/schedule', icon: <CalendarOutlined />, label: '本周课表' },
  ];
  const entries = isAdminPortal && user.role === 'ADMIN' ? [
    { key: '/', icon: <DashboardOutlined />, label: '总览仪表盘' },
    { key: '/students', icon: <TeamOutlined />, label: '学生 CRM' },
    { key: '/teachers', icon: <UserOutlined />, label: '教师管理' },
    { key: '/catalog', icon: <BookOutlined />, label: '课程管理' },
    { key: '/rooms', icon: <ApartmentOutlined />, label: '教室资源' },
    { key: '/demand', icon: <FormOutlined />, label: '选课需求池' },
    { key: '/offering-reviews', icon: <FileDoneOutlined />, label: '开课审核管理' },
    { key: '/scheduling', icon: <ThunderboltOutlined />, label: '智能排课工作台' },
    { key: '/schedule', icon: <CalendarOutlined />, label: '全机构课表' },
    { key: '/approvals', icon: <FileDoneOutlined />, label: '审批中心' },
    { key: '/changes', icon: <WarningOutlined />, label: '异常事件中心' },
    { key: '/analytics', icon: <ExperimentOutlined />, label: '运营数据分析' },
    { key: '/rules', icon: <SettingOutlined />, label: '规则配置' },
    { key: '/audit', icon: <AuditOutlined />, label: '审计日志' },
  ] : user.role === 'TEACHER' ? [
    ...common,
    { key: '/reviews', icon: <FileDoneOutlined />, label: '开课审核' },
    { key: '/classes', icon: <TeamOutlined />, label: '我的班级' },
    { key: '/requests', icon: <FormOutlined />, label: '请假调课' },
    { key: '/changes', icon: <WarningOutlined />, label: '异常工单' },
  ] : [
    ...common,
    { key: '/courses', icon: <BookOutlined />, label: '课程选课' },
    { key: '/applications', icon: <FormOutlined />, label: '我的申请' },
  ];
  entries.push({ key: '/notifications', icon: <BellOutlined />, label: '通知中心' });
  entries.push({ key: '/account', icon: <LockOutlined />, label: '账号与安全' });
  if (user.role !== 'ADMIN') entries.push({ key: '/profile', icon: <UserOutlined />, label: '个人信息与时段' });
  async function logout() {
    try { await authClient.post('/auth/logout'); } finally {
      useAuth.getState().clear(); cache.clear(); navigate('/login', { replace: true });
    }
  }
  return <Layout className="app-shell">
    {!mobile && <Layout.Sider theme="light" width={220}><Link className="app-logo" to="/"><span className="logo-square">E</span><span className="logo-text">EduFlow <small>AI</small></span></Link>
      <Menu theme="light" mode="inline" selectedKeys={[location.pathname]} items={entries.map((item) => ({
        ...item, label: <Link to={item.key}>{item.label}</Link>,
      }))} /><div className="sidebar-footer">本地演示 · 公网关闭</div></Layout.Sider>}
    <Drawer title="EduFlow 导航" placement="left" width={280} open={navigation} onClose={() => setNavigation(false)}>
      <Menu mode="inline" selectedKeys={[location.pathname]} onClick={() => setNavigation(false)} items={entries.map((item) => ({ ...item, label: <Link to={item.key}>{item.label}</Link> }))} />
    </Drawer>
    <Layout className="app-main">
      <Layout.Header className="app-header"><Space>{mobile && <Button aria-label="打开导航" icon={<MenuOutlined />} onClick={() => setNavigation(true)} />}<span className="org-name">{bootstrap?.organization.name || 'EduFlow AI'}</span><Tag bordered={false} color="blue">{labels[user.role]}端</Tag></Space>
        <Space size={18}><AgentDrawer /><Badge count={notifications.filter((n) => !n.read_at).length} size="small"><Button type="text" aria-label="通知中心" icon={<BellOutlined />} onClick={() => navigate('/notifications')} /></Badge>
          <Dropdown menu={{ items: [{ key: 'logout', label: '退出登录', icon: <LogoutOutlined /> }], onClick: logout }}>
            <Button type="text" aria-label="账号菜单"><Space><Avatar size={30} style={{ background: '#e8effa', color: '#245ba6' }}>{user.name.slice(-1)}</Avatar><span className="user-name">{user.name}</span></Space></Button>
          </Dropdown></Space>
      </Layout.Header>
      <Layout.Content className="app-content"><Outlet /></Layout.Content>
    </Layout>
  </Layout>;
}
