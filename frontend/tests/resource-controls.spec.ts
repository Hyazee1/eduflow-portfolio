import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import type { Data } from '../src/types';

const base = process.env.EDUFLOW_E2E_URL || 'http://127.0.0.1:8001';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const evidence = path.join(root, '.runtime/private/evidence');

test('administrator manages room hours, pauses a lesson and sees live workorder status', async ({ page, request }) => {
  expect(base, 'Mutating browser tests require the isolated test API').toBe('http://127.0.0.1:8001');
  const info = JSON.parse(fs.readFileSync(path.join(root, '.runtime/e2e-info.json'), 'utf8'));
  expect(info.database).toBe('eduflow_ai_e2e_test');
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto('/login');
  await page.getByText('管理员', { exact: true }).click();
  await page.getByLabel('账号', { exact: true }).fill('admin');
  await page.getByLabel('密码', { exact: true }).fill(process.env.EDUFLOW_DEMO_PASSWORD || 'EduFlow-Demo-2026!');
  await page.getByRole('button', { name: '登录工作台' }).click();
  await expect(page).toHaveURL(base + '/');
  const session = await request.post('/api/v1/auth/login', { headers: { 'X-EduFlow-Request': '1' }, data: { username: 'admin', role: 'ADMIN', password: process.env.EDUFLOW_DEMO_PASSWORD || 'EduFlow-Demo-2026!' } });
  expect(session.ok()).toBeTruthy();
  const token = (await session.json()).access_token;

  async function api(route: string, body?: Data): Promise<Data> {
    const headers = { Authorization: 'Bearer ' + token, 'Idempotency-Key': crypto.randomUUID() };
    const response = body ? await request.post('/api/v1' + route, { headers, data: body })
      : await request.get('/api/v1' + route, { headers });
    const data = await response.json();
    expect(response.ok(), route + ' -> ' + JSON.stringify(data)).toBeTruthy();
    return data;
  }

  const schedule = await api('/admin/schedule?week=' + info.week);
  const version = schedule.versions.find((row: Data) => row.status === 'ACTIVE');
  expect(version).toBeTruthy();
  const lessons = schedule.sessions.filter((lesson: Data) => lesson.version_id === version.id && lesson.status === 'SCHEDULED');
  const activeVersions = (await api('/admin/versions?page_size=500')).items.filter((v: Data) => v.status === 'ACTIVE');
  const occupied = (await Promise.all(activeVersions.map((v: Data) => api('/admin/schedule/versions/' + v.id))))
    .flatMap((v: Data) => v.lessons.filter((lesson: Data) => lesson.status === 'SCHEDULED'));
  const room = (await api('/admin/rooms?page_size=100')).items[0];
  const hours = await api('/admin/rooms/' + room.id + '/availability');
  const free = hours.availability.find((slot: Data) => !occupied.some((lesson: Data) => lesson.room_id === room.id
    && (new Date(lesson.day + 'T00:00:00Z').getUTCDay() + 6) % 7 === slot.day_index
    && lesson.slot_code === slot.slot_code));
  expect(free).toBeTruthy();
  const key = free.day_index + ':' + free.slot_code;
  await page.goto('/rooms');
  const roomRow = page.locator('tr[data-row-key="' + room.id + '"]');
  await roomRow.getByRole('button', { name: '开放时段', exact: true }).click();
  let dialog = page.getByRole('dialog');
  let checkbox = dialog.locator('input[type="checkbox"][value="' + key + '"]');
  await expect(checkbox).toBeChecked();
  await checkbox.locator('xpath=ancestor::label').click();
  await expect(checkbox).not.toBeChecked();
  await page.screenshot({ path: path.join(evidence, 'room-hours.png'), fullPage: true });
  let saved = page.waitForResponse((response) => response.url().endsWith('/api/v1/admin/rooms/' + room.id + '/availability') && response.request().method() === 'POST');
  await dialog.getByRole('button', { name: '保存开放时段' }).click();
  expect((await saved).ok()).toBeTruthy();
  expect((await api('/admin/rooms/' + room.id + '/availability')).availability.some((slot: Data) => slot.day_index === free.day_index && slot.slot_code === free.slot_code)).toBeFalsy();
  await expect(dialog).toBeHidden();
  await roomRow.getByRole('button', { name: '开放时段', exact: true }).click();
  dialog = page.getByRole('dialog');
  checkbox = dialog.locator('input[type="checkbox"][value="' + key + '"]');
  await expect(checkbox).not.toBeChecked();
  await checkbox.locator('xpath=ancestor::label').click();
  await expect(checkbox).toBeChecked();
  saved = page.waitForResponse((response) => response.url().endsWith('/api/v1/admin/rooms/' + room.id + '/availability') && response.request().method() === 'POST');
  await dialog.getByRole('button', { name: '保存开放时段' }).click();
  expect((await saved).ok()).toBeTruthy();
  await expect(dialog).toBeHidden();

  const lesson = lessons.filter((row: Data) => row.course_group === 'QA').sort((a: Data, b: Data) => b.day.localeCompare(a.day))[0];
  expect(lesson).toBeTruthy();
  await page.goto('/schedule');
  const card = page.getByTestId('lesson-' + lesson.id);
  await card.getByRole('button', { name: '紧急停课', exact: true }).click();
  dialog = page.getByRole('dialog');
  await dialog.getByPlaceholder('说明紧急停课原因').fill('演示场地安全检查，暂停本节答疑');
  const paused = page.waitForResponse((response) => response.url().endsWith('/api/v1/admin/sessions/' + lesson.record_id + '/pause'));
  await dialog.getByRole('button', { name: '暂停并通知师生' }).click();
  expect((await paused).ok()).toBeTruthy();
  await expect(dialog).toBeHidden();
  await expect(card.getByText('紧急暂停', { exact: true })).toBeVisible();
  const updated = await api('/admin/schedule/versions/' + version.id);
  expect(updated.version).toBeGreaterThan(version.version);
  const emergency = (await api('/admin/change-requests') as unknown as Data[]).find((change: Data) => change.kind === 'EMERGENCY_PAUSE' && change.payload.record_id === lesson.record_id)!;
  expect(emergency.status).toBe('APPLIED');
  expect(emergency.events.at(-1).to_state).toBe('APPLIED');
  await page.screenshot({ path: path.join(evidence, 'emergency-pause.png'), fullPage: true });

  const next = lessons.find((row: Data) => row.id !== lesson.id)!;
  const change = await api('/change-requests', { version_id: version.id, kind: 'RESCHEDULE', reason: '验证跨端进度同步', payload: { session_ids: [next.id] } });
  await page.goto('/changes');
  await page.locator('tr[data-row-key="' + change.id + '"]').getByRole('button', { name: '查看进度' }).click();
  dialog = page.getByRole('dialog');
  await expect(dialog.locator('.ant-descriptions').getByText('待管理员审批', { exact: true })).toBeVisible();
  await api('/change-requests/' + change.id + '/cancel', { reason: '从另一个业务请求撤回，验证弹窗同步' });
  await expect(dialog.locator('.ant-descriptions').getByText('已取消', { exact: true })).toBeVisible({ timeout: 15_000 });
  await page.screenshot({ path: path.join(evidence, 'live-workorder.png'), fullPage: true });
  expect(errors).toEqual([]);
  fs.writeFileSync(path.join(evidence, 'resource-controls.json'), JSON.stringify({ database: info.database,
    room_hours_saved_and_restored: true, emergency_pause_applied: true, workorder_auto_refresh: true, browser_errors: errors }, null, 2));
});
