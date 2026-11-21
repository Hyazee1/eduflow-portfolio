import dayjs from 'dayjs';
import { Button, Tag, Tooltip } from 'antd';
import { EditOutlined } from '@ant-design/icons';
import { labels, weekdays, type Lesson, type Slot, type TimeBreak } from '../types';
import { Status } from './Common';

export function WeekGrid({ week, slots, breaks, lessons, onEdit, onPause }: {
  week: string; slots: Slot[]; breaks: TimeBreak[]; lessons: Lesson[]; onEdit?: (lesson: Lesson) => void; onPause?: (lesson: Lesson) => void;
}) {
  const ordered = slots.slice().sort((a, b) => a.ordinal - b.ordinal);
  function breakLabel(slot: Slot, next?: Slot) {
    return breaks.filter((pause) => pause.start >= slot.end && (!next || pause.end <= next.start)).map((pause) => {
      const minute = (time: string) => Number(time.split(':')[0]) * 60 + Number(time.split(':')[1]);
      const minutes = minute(pause.end) - minute(pause.start);
      const name = pause.kind === 'LUNCH' ? '午休' : pause.kind === 'BUFFER' ? '缓冲与整理' : minutes >= 20 ? '大课间' : '课间';
      return pause.start + '—' + pause.end + ' ' + name + ' · ' + minutes + ' 分钟';
    }).join('　/　');
  }
  return <div className="week-scroll"><table className="week-grid" data-testid="week-grid">
    <thead><tr><th>教学时段</th>{weekdays.map((name, i) => <th key={name}>{name}<small>{dayjs(week).add(i, 'day').format('MM / DD')}</small></th>)}</tr></thead>
    <tbody>{ordered.map((slot, index) => (
      <GridRow key={slot.code} slot={slot} week={week} lessons={lessons} onEdit={onEdit} onPause={onPause} breakLabel={breakLabel(slot, ordered[index + 1])} />
    ))}</tbody>
  </table></div>;
}
function GridRow({ slot, week, lessons, onEdit, onPause, breakLabel }: {
  slot: Slot; week: string; lessons: Lesson[]; onEdit?: (lesson: Lesson) => void; onPause?: (lesson: Lesson) => void; breakLabel?: string;
}) {
  return <><tr className={slot.kind === 'QA' ? 'qa-row' : ''}>
    <th className="slot-label"><strong>{slot.code}</strong><span>{slot.start}—{slot.end}</span><small>{slot.duration_minutes} 分钟{slot.kind === 'QA' ? ' · 答疑专用' : ''}</small></th>
    {weekdays.map((_, day) => <td key={day}><div className="cell-stack">
      {lessons.filter((l) => l.day === dayjs(week).add(day, 'day').format('YYYY-MM-DD') && l.slot_code === slot.code).map((lesson) => (
        <article className={'lesson-card group-' + lesson.course_group + (lesson.status === 'CANCELLED' ? ' cancelled' : '')}
          key={lesson.version_id + lesson.id} data-testid={'lesson-' + lesson.id}>
          <div className="lesson-heading"><strong>{lesson.course_name}</strong>{onEdit && <Tooltip title="调整候选课次"><Button size="small" type="text" icon={<EditOutlined />} onClick={() => onEdit(lesson)} /></Tooltip>}</div>
          <div>{lesson.teacher_name} · {lesson.room_name}</div>
          <small>{lesson.start_time}—{lesson.end_time} · {lesson.student_count} 人</small>
          <div><Tag>{labels[lesson.course_group]}</Tag>{lesson.status !== 'SCHEDULED' ? <Status value={lesson.status} />
            : lesson.version_status === 'PUBLISHED_PENDING_CONFIRMATION' && <Status value={lesson.confirmation || lesson.version_status} />}</div>
          {onPause && lesson.version_status === 'ACTIVE' && lesson.status === 'SCHEDULED' && <Button type="link" danger size="small" onClick={() => onPause(lesson)}>紧急停课</Button>}
        </article>
      ))}
    </div></td>)}
  </tr>{breakLabel && <tr className="break-row"><td colSpan={6}>{breakLabel}</td></tr>}</>;
}
