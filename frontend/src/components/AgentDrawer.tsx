import { useState } from 'react';
import { Button, Drawer, Input, Space, Tag } from 'antd';
import { RobotOutlined, SendOutlined } from '@ant-design/icons';
import { post } from '../api/client';
import type { Data } from '../types';

export default function AgentDrawer() {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [messages, setMessages] = useState<{ role: string; content: string }[]>([]);
  const [mode, setMode] = useState('offline');
  async function send() {
    if (!text.trim() || busy) return;
    const input = text; setText(''); setBusy(true);
    setMessages((items) => [...items, { role: 'user', content: input }]);
    try {
      const data = await post<Data>('/agent/chat', { message: input, context: {} });
      setMode(data.mode);
      setMessages((items) => [...items, { role: 'assistant', content: data.message }]);
    } finally { setBusy(false); }
  }
  return <>
    <Button icon={<RobotOutlined />} onClick={() => setOpen(true)}>AI 助手</Button>
    <Drawer title={<Space>运营助手<Tag color="blue">{mode === 'offline' ? '离线规则模式' : '在线模型'}</Tag></Space>} width={470} open={open} onClose={() => setOpen(false)}>
      <p className="muted">可查询课表、分析负载和生成调课建议。方案经过审批与确认后才会生效。</p>
      <div className="chat-messages">{messages.length ? messages.map((item, i) => <div key={i} className={'chat-bubble ' + item.role}>{item.content}</div>)
        : <div className="chat-bubble">试试输入“统计本周课程”，或“分析工单 + 事务编号”。</div>}</div>
      <Space.Compact style={{ width: '100%' }}><Input value={text} maxLength={4000} placeholder="描述你的需求" onChange={(e) => setText(e.target.value)} onPressEnter={send} />
        <Button type="primary" icon={<SendOutlined />} loading={busy} onClick={send}>发送</Button></Space.Compact>
    </Drawer>
  </>;
}
