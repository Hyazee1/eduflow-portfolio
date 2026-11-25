import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import type { Data } from '../src/types';

const base = process.env.EDUFLOW_E2E_URL || 'http://127.0.0.1:8001';
const publicBase = process.env.EDUFLOW_E2E_PUBLIC_URL || 'http://127.0.0.1:8101';
const tokenOrigins = new Map<string,string>();
const password = process.env.EDUFLOW_DEMO_PASSWORD || 'EduFlow-Demo-2026!';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const info = JSON.parse(fs.readFileSync(path.join(root, '.runtime/e2e-info.json'), 'utf8'));
const evidence = path.join(root, '.runtime/private/evidence');

async function api(route: string, token?: string, body?: Data): Promise<Data> {
  const origin = route === '/auth/login' ? body?.role === 'ADMIN' ? base : publicBase : tokenOrigins.get(token || '') || base;
  const response = await fetch(origin + '/api/v1' + route, {
    method: body ? 'POST' : 'GET',
    headers: { Connection: 'close', 'Content-Type': 'application/json', ...(token ? { Authorization: 'Bearer ' + token } : {}), ...(body ? { 'Idempotency-Key': crypto.randomUUID() } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json();
  expect(response.ok, route + ' -> ' + JSON.stringify(data)).toBeTruthy();
  return data;
}

async function apiLogin(username: string, role: string) {
  const value = await api('/auth/login', undefined, { username, password, role });
  tokenOrigins.set(value.access_token, role === 'ADMIN' ? base : publicBase);
  return value;
}

async function uiLogin(page: Page, username: string, role: string) {
  const origin = role === 'ADMIN' ? base : publicBase;
  await page.goto(origin + '/login');
  await page.getByText({ ADMIN: '管理员', TEACHER: '教师', STUDENT: '学生' }[role]!, { exact: true }).click();
  await page.getByLabel('账号', { exact: true }).fill(username);
  await page.getByLabel('密码', { exact: true }).fill(password);
  const response = page.waitForResponse((r) => r.url().endsWith('/api/v1/auth/login') && r.request().method() === 'POST');
  await page.getByRole('button', { name: '登录工作台' }).click();
  const result = await (await response).json();
  expect(result.access_token).toBeTruthy();
  await expect(page).toHaveURL(origin + '/');
  return apiLogin(username, role);
}

async function confirmRemaining(adminToken: string, versionId: string, users: Data[]) {
  const version = await api('/admin/schedule/versions/' + versionId, adminToken);
  const pending = new Map<string, string[]>();
  for (const row of version.confirmations.filter((c: Data) => c.decision === 'PENDING')) {
    pending.set(row.user_id, [...(pending.get(row.user_id) || []), row.logical_id]);
  }
  const entries = [...pending.entries()];
  for (let i = 0; i < entries.length; i += 6) {
    await Promise.all(entries.slice(i, i + 6).map(async ([userId, sessions]) => {
      const user = users.find((u) => u.id === userId)!;
      const login = await apiLogin(user.username, user.role);
      await api('/schedule-confirmations', login.access_token, { version_id: versionId, session_ids: sessions, decision: 'ACCEPTED' });
    }));
  }
  return api('/admin/schedule/versions/' + versionId, adminToken);
}

function selectMove(lessons: Data[], courses: Data[], rooms: Data[], week: string) {
  for (const lesson of lessons.filter((s) => s.slot_code === 'S7' && s.status === 'SCHEDULED')) {
    if (lessons.filter((s) => s.day === lesson.day && s.slot_code === 'S7' && s.status === 'SCHEDULED').length < 2) continue;
    const course = courses.find((c) => c.id === lesson.course_id)!;
    const own = lessons.filter((s) => s.teacher_id === lesson.teacher_id && s.day === lesson.day && s.status === 'SCHEDULED');
    for (const slot of ['S5', 'S6', 'S4', 'S3', 'S2', 'S1']) {
      const collisions = lessons.filter((s) => s.day === lesson.day && s.slot_code === slot && s.status === 'SCHEDULED');
      if (collisions.some((s) => s.teacher_id === lesson.teacher_id || s.students.some((id: string) => lesson.students.includes(id)))) continue;
      const positions = [...own.filter((s) => s.id !== lesson.id).map((s) => Number(s.slot_code.slice(1))), Number(slot.slice(1))].sort((a, b) => a - b);
      if (positions.filter((s) => s <= 4).length >= 4 || positions.filter((s) => s >= 5).length >= 4) continue;
      if (positions.includes(8) && positions.filter((s) => s >= 5 && s <= 7).length >= 3) continue;
      let streak = 1;
      let tooLong = false;
      for (let i = 1; i < positions.length; i++) {
        streak = positions[i] === positions[i - 1] + 1 ? streak + 1 : 1;
        if (streak > 3) tooLong = true;
      }
      if (tooLong) continue;
      const room = rooms.find((r) => r.status === 'OPEN' && r.capacity >= lesson.students.length
        && course.features.every((feature: string) => r.features.includes(feature))
        && !collisions.some((s) => s.room_id === r.id));
      if (room) return { lesson, slot, room, day: lesson.day, week };
    }
  }
  // A sole S7 class must keep its slot. Changing its room is a valid local adjustment.
  for (const lesson of lessons.filter(s => s.slot_code === 'S7' && s.status === 'SCHEDULED')) {
    const course = courses.find(c => c.id === lesson.course_id)!;
    const room = rooms.find(r => r.id !== lesson.room_id && r.status === 'OPEN' && r.capacity >= lesson.students.length
      && course.features.every((feature: string) => r.features.includes(feature))
      && !lessons.some(s => s.day === lesson.day && s.slot_code === 'S7' && s.status === 'SCHEDULED' && s.room_id === r.id));
    if (room) return { lesson, slot: 'S7', room, day: lesson.day, week };
  }
  throw new Error('No independently screened S7 adjustment target found');
}

test('27-step real API, PostgreSQL, Celery and three-role browser journey', async ({ browser }) => {
  const steps: { step: number; result: string }[] = [];
  async function step(number: number, title: string, work: () => Promise<void>) {
    console.log('Step ' + number + ': ' + title);
    await test.step(number + '. ' + title, work);
    steps.push({ step: number, result: title + '：通过' });
  }
  const contexts = [await browser.newContext({ baseURL: base }), await browser.newContext({ baseURL: publicBase }), await browser.newContext({ baseURL: publicBase })];
  const [adminPage, studentPage, teacherPage] = await Promise.all(contexts.map((context) => context.newPage()));
  const pageErrors: string[] = [];
  for (const context of contexts) context.on('page', (page) => page.on('pageerror', (error) => pageErrors.push(error.message)));
  for (const page of [adminPage, studentPage, teacherPage]) page.on('pageerror', (error) => pageErrors.push(error.message));
  let admin: Data; let student: Data; let teacher: Data; let teacherUser: Data;
  let users: Data[] = []; let enrollment: Data; let versionId = ''; let version: Data = {};
  let courses: Data[] = []; let move: Data; let change: Data; let adjustedId = '';
  try {
    await step(1, '一键启动后的服务健康', async () => {
      const health = await fetch(base + '/health/ready');
      expect(health.status).toBe(200);
      expect((await health.json()).status).toBe('ready');
    });
    await step(2, '打开真实登录页', async () => {
      await studentPage.goto('/login');
      await expect(studentPage.getByRole('button', { name: '登录工作台' })).toBeVisible();
      await studentPage.screenshot({ path: path.join(evidence, 'login.png'), fullPage: true });
    });
    await step(3, '学生登录并选择编程与实验', async () => {
      student = await uiLogin(studentPage, 'student001', 'STUDENT');
      admin = await uiLogin(adminPage, 'admin', 'ADMIN');
      users = (await api('/admin/users?page_size=500', admin.access_token)).items;
      courses = await api('/courses', admin.access_token) as unknown as Data[];
      await studentPage.goto('/courses');
      const card = studentPage.getByRole('heading', { name: '编程与实验', exact: true }).locator('..');
      await card.getByRole('button', { name: '选择课程' }).click();
    });
    await step(4, '提交周三 S3 意向', async () => {
      const modal = studentPage.locator('.ant-modal-content');
      await modal.getByText('周三', { exact: true }).locator('..').getByRole('checkbox', { name: 'S3 10:00', exact: true }).check();
      const response = studentPage.waitForResponse((r) => r.url().endsWith('/api/v1/enrollment-requests') && r.request().method() === 'POST');
      await studentPage.getByRole('button', { name: '确认并提交申请' }).click();
      enrollment = await (await response).json();
      expect(enrollment.status).toBe('PENDING_REVIEW');
    });
    await step(5, '统一 S3 为 10:00—10:45', async () => {
      const bootstrap = await api('/bootstrap', student.access_token);
      expect(bootstrap.slots.find((s: Data) => s.code === 'S3')).toMatchObject({ start: '10:00', end: '10:45' });
      const requests = await api('/enrollment-requests/mine', student.access_token) as unknown as Data[];
      expect(requests.find((r) => r.id === enrollment.id)!.preferences[0]).toMatchObject({ day_index: 2, slot_code: 'S3' });
    });
    let decisionId = '';
    await step(6, '教师收到真实审核申请', async () => {
      const teachers = (await api('/admin/teachers?page_size=500', admin.access_token)).items;
      teacherUser = teachers.find((t: Data) => t.course_ids.includes(enrollment.course_id));
      teacher = await uiLogin(teacherPage, teacherUser.username, 'TEACHER');
      const reviews = await api('/teachers/me/offering-reviews', teacher.access_token) as unknown as Data[];
      decisionId = reviews.find((r) => r.offering.id === enrollment.offering_id)!.id;
      await teacherPage.goto('/reviews');
      await expect(teacherPage.locator('tr[data-row-key="' + decisionId + '"]')).toBeVisible();
    });
    await step(7, '教师同意开课', async () => {
      const response = teacherPage.waitForResponse((r) => r.url().endsWith('/api/v1/teacher-decisions'));
      await teacherPage.locator('tr[data-row-key="' + decisionId + '"]').getByRole('button', { name: '同意开课' }).click();
      expect((await (await response).json()).decision).toBe('ACCEPTED');
    });
    await step(8, '管理员触发真实 Celery 排课', async () => {
      await adminPage.goto('/scheduling');
      const response = adminPage.waitForResponse((r) => r.url().endsWith('/api/v1/admin/schedule/solve'));
      await adminPage.getByRole('button', { name: '自动排课' }).click();
      const job = await (await response).json();
      await expect.poll(async () => {
        const data = await api('/admin/schedule/jobs/' + job.id, admin.access_token);
        expect(['FAILED', 'STALE']).not.toContain(data.status);
        if (data.version_id) versionId = data.version_id;
        return data.status;
      }, { timeout: 130_000, intervals: [1000, 2000] }).toBe('COMPLETED');
      expect(versionId).toBeTruthy();
    });
    await step(9, '联合匹配教师学生教室和时段', async () => {
      version = await api('/admin/schedule/versions/' + versionId, admin.access_token);
      expect(['OPTIMAL', 'FEASIBLE']).toContain(version.solver_status);
      expect(version.lessons.every((s: Data) => s.teacher_id && s.room_id && s.students.length > 0)).toBeTruthy();
      expect(version.report.metrics.satisfaction_rate).toBe(1);
      expect(version.report.metrics.covered_slots).toBe(40);
    });
    await step(10, '查看独立冲突报告', async () => {
      await adminPage.goto('/scheduling?version=' + versionId);
      await adminPage.getByRole('tab', { name: '求解与质量报告' }).click();
      expect(version.report.hard_conflicts).toBe(0);
      await adminPage.screenshot({ path: path.join(evidence, 'quality-report.png'), fullPage: true });
    });
    await step(11, '管理员发布待确认课表', async () => {
      await adminPage.getByRole('button', { name: '校验并发布' }).click();
      const response = adminPage.waitForResponse((r) => r.url().endsWith('/publish'));
      await adminPage.getByRole('button', { name: '发布并通知' }).click();
      expect((await (await response).json()).status).toBe('PUBLISHED_PENDING_CONFIRMATION');
    });
    await step(12, '教师学生分别在界面确认并收齐其余参与者', async () => {
      for (const page of [teacherPage, studentPage]) {
        await page.goto('/schedule');
        const response = page.waitForResponse((r) => r.url().endsWith('/api/v1/schedule-confirmations'));
        await page.getByRole('button', { name: '确认本人全部课次' }).click();
        expect((await response).ok()).toBeTruthy();
      }
      version = await confirmRemaining(admin.access_token, versionId, users);
      expect(version.status).toBe('ACTIVE');
    });
    await step(13, '学生只看到本人课表', async () => {
      await studentPage.reload();
      const schedule = await api('/students/me/schedule?week=' + info.week, student.access_token);
      expect(schedule.sessions.every((s: Data) => s.students.every((id: string) => id === student.user.id))).toBeTruthy();
      await expect(studentPage.getByRole('heading', { name: '本周课表' })).toBeVisible();
    });
    await step(14, '教师看到本人授课与休息', async () => {
      await teacherPage.reload();
      await expect(teacherPage.getByText('本人每日授课与休息', { exact: true })).toBeVisible();
    });
    await step(15, '管理员查看机构总课表', async () => {
      await adminPage.goto('/schedule');
      await expect(adminPage.getByRole('heading', { name: '全机构课表' })).toBeVisible();
      await adminPage.screenshot({ path: path.join(evidence, 'admin-week.png'), fullPage: true });
    });
    await step(16, '教师申请调整 S7 课次', async () => {
      const rooms = (await api('/admin/rooms?page_size=500', admin.access_token)).items;
      move = selectMove(version.lessons, courses, rooms, info.week);
      const teacherRecord = users.find((u) => u.id === move.lesson.teacher_id)!;
      teacher = await uiLogin(teacherPage, teacherRecord.username, 'TEACHER');
      await teacherPage.goto('/requests');
      await teacherPage.getByRole('button', { name: '提交异常申请' }).click();
      await teacherPage.locator('.ant-select').filter({ has: teacherPage.getByLabel('申请类型') }).click();
      await teacherPage.locator('.ant-select-dropdown:visible').getByText('调整时间', { exact: true }).click();
      await teacherPage.locator('.ant-select').filter({ has: teacherPage.getByLabel('受影响课次') }).click();
      await teacherPage.locator('.ant-select-dropdown:visible').getByText(new RegExp(move.lesson.day + ' S7')).click();
      await teacherPage.getByLabel('希望调整至日期').fill(move.day);
      await teacherPage.getByLabel('希望调整至日期').press('Enter');
      await teacherPage.locator('.ant-select').filter({ has: teacherPage.getByLabel('目标教学时段') }).click();
      await teacherPage.locator('.ant-select-dropdown:visible').getByText(new RegExp('^' + move.slot + ' ')).click();
      await teacherPage.locator('.ant-select').filter({ has: teacherPage.getByLabel('希望更换的教室（可选）') }).click();
      await teacherPage.locator('.ant-select-dropdown:visible').getByText(move.room.name, { exact: true }).click();
      await teacherPage.getByLabel('申请原因').fill('教学安排调整，申请将下午 S7 课程调整至共同可用的时段和教室');
      const response = teacherPage.waitForResponse((r) => r.url().endsWith('/api/v1/change-requests') && r.request().method() === 'POST');
      await teacherPage.getByRole('button', { name: '提交申请', exact: true }).click();
      change = await (await response).json();
      expect(change.status).toBe('PENDING_ADMIN');
    });
    await step(17, 'S7 严格为 16:00—16:45', async () => {
      expect(move.lesson).toMatchObject({ slot_code: 'S7', start: '16:00', end: '16:45' });
    });
    await step(18, '离线 Agent 调用真实求解器生成方案', async () => {
      await teacherPage.getByRole('button', { name: 'AI 助手' }).click();
      await teacherPage.getByPlaceholder('描述你的需求').fill('分析工单 ' + change.id);
      const [response] = await Promise.all([
        teacherPage.waitForResponse((r) => r.url().endsWith('/api/v1/agent/chat'), { timeout: 45_000 }),
        teacherPage.getByRole('button', { name: '发送' }).click(),
      ]);
      const data = await response.json();
      expect(data.mode).toBe('offline');
      const result = data.tool_calls.find((c: Data) => c.name === 'simulate_reschedule').result;
      expect(['FEASIBLE', 'OPTIMAL']).toContain(result.proposal.solver_status);
      expect(result.proposal.violations).toEqual([]);
      await teacherPage.screenshot({ path: path.join(evidence, 'agent-proposal.png'), fullPage: true });
    });
    await step(19, '管理员审批同一事务方案', async () => {
      await adminPage.goto('/approvals');
      await adminPage.locator('tr[data-row-key="' + change.id + '"]').getByRole('button', { name: '查看进度' }).click();
      const response = adminPage.waitForResponse((r) => r.url().endsWith('/decision'));
      await adminPage.getByRole('button', { name: '批准并请求相关人员确认' }).click();
      expect((await (await response).json()).status).toBe('WAITING_CONFIRMATION');
      const versions = (await api('/admin/versions?week=' + info.week + '&page_size=100', admin.access_token)).items;
      adjustedId = versions.find((v: Data) => v.change_request_id === change.id).id;
    });
    await step(20, '仅受影响人员重新确认', async () => {
      const pending = await api('/admin/schedule/versions/' + adjustedId, admin.access_token);
      expect(pending.confirmations.some((c: Data) => c.inherited_from)).toBeTruthy();
      expect(pending.confirmations.filter((c: Data) => c.decision === 'PENDING').length).toBeGreaterThan(0);
      await teacherPage.goto('/schedule');
      const confirmation = teacherPage.waitForResponse((response) => response.url().endsWith('/api/v1/schedule-confirmations') && response.request().method() === 'POST');
      await teacherPage.getByRole('button', { name: '确认本人全部课次' }).click();
      expect((await confirmation).ok()).toBeTruthy();
      version = await confirmRemaining(admin.access_token, adjustedId, users);
      expect(version.status).toBe('ACTIVE');
    });
    await step(21, '原子生效并同步各端', async () => {
      expect((await api('/admin/schedule/versions/' + versionId, admin.access_token)).status).toBe('SUPERSEDED');
      const changed = version.lessons.find((s: Data) => s.id === move.lesson.id);
      expect(changed).toMatchObject({ day: move.day, slot_code: move.slot, room_id: move.room.id });
      const unchanged = version.lessons.filter((s: Data) => s.id !== move.lesson.id);
      for (const lesson of unchanged) expect(lesson).toEqual((await api('/admin/schedule/versions/' + versionId, admin.access_token)).lessons.find((s: Data) => s.id === lesson.id));
      await studentPage.reload();
      await teacherPage.reload();
      await adminPage.goto('/schedule');
    });
    await step(22, '存在合法 S8 答疑安排', async () => {
      expect(version.lessons.filter((s: Data) => s.slot_code === 'S8').length).toBeGreaterThan(0);
    });
    await step(23, 'S8 之外不存在答疑课', async () => {
      expect(version.lessons.filter((s: Data) => courses.find((c) => c.id === s.course_id)?.course_group === 'QA').every((s: Data) => s.slot_code === 'S8')).toBeTruthy();
    });
    await step(24, 'S8 不存在普通课', async () => {
      expect(version.lessons.filter((s: Data) => s.slot_code === 'S8').every((s: Data) => courses.find((c) => c.id === s.course_id)?.course_group === 'QA')).toBeTruthy();
    });
    await step(25, '复核教师负载休息与大课间', async () => {
      expect(version.report.hard_conflicts).toBe(0);
      for (const teacherId of new Set(version.lessons.map((s: Data) => s.teacher_id))) {
        const lessons = version.lessons.filter((s: Data) => s.teacher_id === teacherId);
        expect(lessons.length).toBeLessThanOrEqual(24);
        for (const day of new Set(lessons.map((s: Data) => s.day))) expect(lessons.filter((s: Data) => s.day === day).length).toBeLessThanOrEqual(5);
      }
      const slots = (await api('/bootstrap', admin.access_token)).slots;
      expect(slots.find((s: Data) => s.code === 'S2').end).toBe('09:40');
      expect(slots.find((s: Data) => s.code === 'S6').end).toBe('15:40');
    });
    await step(26, '查看真实审计与完整异常时间线', async () => {
      const detail = await api('/change-requests/' + change.id, admin.access_token);
      expect(detail.status).toBe('APPLIED');
      expect(detail.events.map((e: Data) => e.to_state)).toEqual(['SUBMITTED', 'VALIDATING', 'PENDING_ADMIN', 'APPROVED', 'WAITING_CONFIRMATION', 'APPLIED']);
      await adminPage.goto('/audit');
      await expect(adminPage.getByRole('heading', { name: '审计日志' })).toBeVisible();
      await adminPage.goto('/changes');
      await adminPage.locator('tr[data-row-key="' + change.id + '"]').getByRole('button', { name: '查看进度' }).click();
      await adminPage.screenshot({ path: path.join(evidence, 'change-timeline.png'), fullPage: true });
    });
    await step(27, '课程结束后在界面保存出勤与反馈', async () => {
      const schedule = await api('/teachers/me/schedule?week=' + info.week, teacher.access_token);
      const lesson = schedule.sessions.find((s: Data) => s.id === move.lesson.id && s.version_status === 'ACTIVE');
      const end = new Date(lesson.day + 'T' + lesson.end_time + ':00+08:00');
      end.setMinutes(end.getMinutes() + 1);
      expect((await fetch(base + '/__test__/clock', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ now: end.toISOString() }) })).ok).toBeTruthy();
      const teacherRecord = users.find((u) => u.id === move.lesson.teacher_id)!;
      teacher = await uiLogin(teacherPage, teacherRecord.username, 'TEACHER');
      await teacherPage.goto('/classes');
      const row = teacherPage.locator('tr[data-row-key="' + lesson.record_id + '"]');
      await expect(teacherPage.locator('.ant-table-tbody .ant-table-row').first()).toBeVisible();
      for (let attempt = 0; !await row.isVisible() && attempt < 2; attempt++) {
        await teacherPage.locator('.ant-pagination-next:not(.ant-pagination-disabled) button').click();
      }
      await expect(row).toBeVisible();
      await row.getByRole('button', { name: '班级名单与出勤' }).click();
      const attendanceResponse = teacherPage.waitForResponse((r) => r.url().endsWith('/api/v1/attendance/batch'));
      await teacherPage.getByRole('button', { name: '保存本班出勤' }).click();
      expect((await attendanceResponse).ok()).toBeTruthy();
      await teacherPage.getByRole('button', { name: '登记反馈', exact: true }).first().click();
      await teacherPage.getByLabel('教学反馈').fill('已完成本次实践任务，课堂参与积极。');
      const feedbackResponse = teacherPage.waitForResponse((r) => r.url().endsWith('/api/v1/feedback'));
      await teacherPage.getByRole('button', { name: '保存并通知学生' }).click();
      expect((await feedbackResponse).ok()).toBeTruthy();
      const roster = await api('/classes/' + lesson.record_id + '/roster', teacher.access_token);
      expect(roster.students.every((s: Data) => s.attendance.length > 0)).toBeTruthy();
      expect(roster.students.some((s: Data) => s.feedback[0]?.content.includes('实践任务'))).toBeTruthy();
      await teacherPage.screenshot({ path: path.join(evidence, 'attendance-feedback.png'), fullPage: true });
    });
    expect(pageErrors).toEqual([]);
    fs.writeFileSync(path.join(evidence, 'demo-27-steps.json'), JSON.stringify({ database: info.database, week: info.week,
      steps, solver_status: version.solver_status, hard_conflicts: version.report.hard_conflicts, browser_errors: pageErrors }, null, 2));
  } finally {
    await Promise.allSettled(contexts.map((context) => context.close()));
  }
});
