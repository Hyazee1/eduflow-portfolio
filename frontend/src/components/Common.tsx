import { Empty, Skeleton, Space, Tag } from 'antd';
import type { ReactNode } from 'react';
import { labels } from '../types';

export function Status({ value }: { value?: string | null }) {
  const color = ['ACTIVE', 'ACCEPTED', 'APPLIED', 'OPTIMAL', 'OPEN', 'PRESENT'].includes(value || '') ? 'green'
    : ['REJECTED', 'FAILED', 'INFEASIBLE', 'MODEL_INVALID', 'SUSPENDED', 'CLOSED'].includes(value || '') ? 'red'
    : ['PUBLISHED_PENDING_CONFIRMATION', 'WAITING_CONFIRMATION', 'PENDING_ADMIN', 'RUNNING'].includes(value || '') ? 'gold' : 'blue';
  return <Tag color={color}>{labels[value || ''] || value || '—'}</Tag>;
}
export function PageTitle({ title, description, actions }: { title: string; description?: string; actions?: ReactNode }) {
  return <div className="page-title"><div><h1>{title}</h1>{description && <p>{description}</p>}</div><Space wrap>{actions}</Space></div>;
}
export function Loading() { return <Skeleton active paragraph={{ rows: 7 }} />; }
export function Nothing({ text = '暂无记录' }: { text?: string }) { return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={text} />; }
