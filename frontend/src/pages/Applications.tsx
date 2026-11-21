import { useQuery } from '@tanstack/react-query';
import { Alert, Card, Table } from 'antd';
import { get } from '../api/client';
import { useAuth } from '../stores/auth';
import { PageTitle, Status } from '../components/Common';
import { weekdays, type Data } from '../types';

export default function Applications() {
  const user = useAuth((s) => s.user)!;
  const query = useQuery({ queryKey: ['enrollments', user.id], queryFn: () => get<Data[]>('/enrollment-requests/mine'), refetchInterval: 5000 });
  if (query.isError) return <Alert type="error" message="无法读取选课申请" />;
  return <><PageTitle title="我的申请" description="跟踪课程需求和教师审核结果；正式安排请查看本周课表" /><Card><Table<Data> rowKey="id" loading={query.isPending} dataSource={query.data || []} columns={[
    { title: '课程名称', dataIndex: 'course_name' }, { title: '上课周', dataIndex: 'week' },
    { title: '意向时段', render: (_, row) => row.preferences?.map((p: Data) => weekdays[p.day_index] + ' ' + p.slot_code).join('、') },
    { title: '审核状态', dataIndex: 'status', render: (value) => <Status value={value} /> },
  ]} /></Card></>;
}
