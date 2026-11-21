import { lazy, Suspense, useEffect } from 'react';
import { Navigate, Outlet, Route, Routes, Link } from 'react-router-dom';
import { Button, Result, Spin } from 'antd';
import { refreshSession } from './api/client';
import { useAuth } from './stores/auth';
import type { Role } from './types';
import AppLayout from './layouts/AppLayout';
import Login from './pages/Login';
import { isAdminPortal } from './portal';

const Dashboard = isAdminPortal ? lazy(() => import('./pages/Dashboard')) : lazy(() => import('./pages/PersonalDashboard'));
const AdminWorkspace = isAdminPortal ? lazy(() => import('./pages/AdminWorkspace')) : null;
const Account = lazy(() => import('./pages/Account'));
const AcceptAccount = lazy(() => import('./pages/Account').then((m) => ({ default: m.AcceptAccount })));
const Schedule = lazy(() => import('./pages/Schedule'));
const Courses = lazy(() => import('./pages/Courses'));
const Reviews = lazy(() => import('./pages/Reviews'));
const Applications = lazy(() => import('./pages/Applications'));
const Changes = lazy(() => import('./pages/Changes'));
const Classes = lazy(() => import('./pages/Classes'));
const Notifications = lazy(() => import('./pages/PersonalUtilities').then((m) => ({ default: m.Notifications })));
const Profile = lazy(() => import('./pages/PersonalUtilities').then((m) => ({ default: m.Profile })));

function RequireSession() {
  const { user, initialized } = useAuth();
  if (!initialized) return <div className="full-loader"><Spin size="large" /></div>;
  return user ? <AppLayout key={user.id} /> : <Navigate to="/login" replace />;
}
function Roles({ allowed }: { allowed: Role[] }) {
  const role = useAuth((s) => s.user?.role);
  return role && allowed.includes(role) ? <Outlet /> : <Result status="403" title="无权访问" subTitle="当前身份不能访问此工作区。" extra={<Link to="/"><Button>返回本人工作台</Button></Link>} />;
}
export default function App() {
  useEffect(() => { refreshSession().catch(() => undefined).finally(() => useAuth.getState().ready()); }, []);
  return <Suspense fallback={<div className="full-loader"><Spin size="large" /></div>}><Routes>
    <Route path="/login" element={<Login />} />
    <Route path="/accept" element={<AcceptAccount />} />
    <Route element={<RequireSession />}>
      <Route index element={<Dashboard />} />
      <Route path="schedule" element={<Schedule />} />
      <Route path="notifications" element={<Notifications />} />
      <Route path="changes" element={<Changes />} />
      <Route path="profile" element={<Profile />} />
      <Route path="account" element={<Account />} />
      <Route element={<Roles allowed={['TEACHER']} />}>
        <Route path="reviews" element={<Reviews />} />
        <Route path="classes" element={<Classes />} />
        <Route path="requests" element={<Changes requests />} />
      </Route>
      {AdminWorkspace && <Route path="*" element={<AdminWorkspace />} />}
      <Route element={<Roles allowed={['STUDENT']} />}>
        <Route path="courses" element={<Courses />} />
        <Route path="applications" element={<Applications />} />
      </Route>
    </Route>
    <Route path="*" element={<Result status="404" title="页面不存在" extra={<Link to="/">返回首页</Link>} />} />
  </Routes></Suspense>;
}
