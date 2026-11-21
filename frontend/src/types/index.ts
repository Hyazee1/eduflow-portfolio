export type Role = 'ADMIN' | 'TEACHER' | 'STUDENT';
export interface User { id: string; name: string; username: string; role: Role; organization_id: string }
export type Data = Record<string, any>;
export interface Slot { id?: string; code: string; start: string; end: string; kind: string; period: string; ordinal: number; duration_minutes: number }
export interface TimeBreak { start: string; end: string; kind: 'BREAK' | 'BUFFER' | 'LUNCH' }
export interface Lesson extends Data {
  id: string; record_id: string; version_id: string; course_id: string; course_name: string;
  course_group: string; teacher_id: string; teacher_name: string; room_id: string; room_name: string;
  day: string; slot_code: string; start_time: string; end_time: string; status: string;
  version_status: string; confirmation: string | null; student_count: number;
}
export interface Schedule extends Data { week: string; versions: Data[]; sessions: Lesson[]; slots: Slot[]; breaks: TimeBreak[]; version_slots?: Record<string, Slot[]> }
export const labels: Record<string, string> = {
  ADMIN: '管理员', TEACHER: '教师', STUDENT: '学生',
  MAIN: '主课', INTEREST: '兴趣', EXTENSION: '课后拓展', QA: '答疑',
  DRAFT: '草稿', VALIDATED: '校验通过', PUBLISHED_PENDING_CONFIRMATION: '待双方确认',
  ACTIVE: '正式生效', CANCELLED: '已取消', SUPERSEDED: '历史版本',
  PENDING: '待处理', PENDING_REVIEW: '待教师审核', APPROVED: '已批准',
  ACCEPTED: '已同意', REJECTED: '已拒绝', ADJUST: '申请调整',
  SUBMITTED: '已提交', VALIDATING: '校验中', PENDING_ADMIN: '待管理员审批',
  WAITING_CONFIRMATION: '等待受影响人员确认', APPLIED: '已应用', FAILED: '失败',
  QUEUED: '排队中', RUNNING: '求解中', COMPLETED: '已完成', STALE: '源数据已变化',
  OPTIMAL: '最优解', FEASIBLE: '可行解', INFEASIBLE: '无解', UNKNOWN: '预算内未找到解',
  MODEL_INVALID: '模型无效', OPEN: '开放', CLOSED: '关闭', MAINTENANCE: '维修',
  SCHEDULED: '已安排', SUSPENDED: '紧急暂停', EXCUSED: '请假',
  PRESENT: '到勤', ABSENT: '缺勤', LATE: '迟到', MERGED: '已合并开课',
  TEACHER_LEAVE: '教师请假', STUDENT_LEAVE: '学生请假', RESCHEDULE: '调整时间',
  ROOM_FAULT: '教室维修', EQUIPMENT_FAULT: '设备故障', SUBSTITUTE: '教师代课',
  CANCEL_SESSION: '取消课程', ENROLLMENT_CHANGE: '调整选课', ADD_SESSION: '临时增课',
  CONFIRMATION_REJECTED: '拒绝课表确认',
  EMERGENCY_PAUSE: '紧急停课',
  COMPUTER: '计算机教室', LAB: '科学实验室', DRONE: '无人机与机器人实验室', ART: '艺术绘画教室',
  CALLIGRAPHY: '书法教室', GENERAL: '普通教学教室', MULTIPURPOSE: '多功能教室',
  computer: '计算机', lab: '实验器材', safety: '安全设施', drone: '无人机', art: '艺术绘画', calligraphy: '书法', mechanics: '机械器材',
};
export const groups = ['MAIN', 'INTEREST', 'EXTENSION', 'QA'];
export const weekdays = ['周一', '周二', '周三', '周四', '周五'];
