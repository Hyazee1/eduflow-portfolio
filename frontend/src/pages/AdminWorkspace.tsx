import { lazy } from 'react';
import { Route, Routes } from 'react-router-dom';
import { Result } from 'antd';
import { useAuth } from '../stores/auth';
const Resources = lazy(() => import('./Resources'));
const Reviews = lazy(() => import('./Reviews'));
const Scheduling = lazy(() => import('./Scheduling'));
const Changes = lazy(() => import('./Changes'));
const Dashboard = lazy(() => import('./Dashboard'));
const Rules = lazy(() => import('./Utilities').then((m) => ({ default: m.RulesPage })));
const Records = lazy(() => import('./Utilities').then((m) => ({ default: m.RecordsPage })));
export default function AdminWorkspace() {
  const role = useAuth((s) => s.user?.role);
  if (role !== 'ADMIN') return <Result status="403" title="无权访问" />;
  return <Routes>
    <Route path="students" element={<Resources kind="students" />} />
    <Route path="teachers" element={<Resources kind="teachers" />} />
    <Route path="catalog" element={<Resources kind="catalog" />} />
    <Route path="rooms" element={<Resources kind="rooms" />} />
    <Route path="demand" element={<Records type="demand" />} />
    <Route path="offering-reviews" element={<Reviews />} />
    <Route path="audit" element={<Records type="audit" />} />
    <Route path="scheduling" element={<Scheduling />} />
    <Route path="approvals" element={<Changes approvals />} />
    <Route path="analytics" element={<Dashboard analytics />} />
    <Route path="rules" element={<Rules />} />
    <Route path="*" element={<Result status="404" title="页面不存在" />} />
  </Routes>;
}
