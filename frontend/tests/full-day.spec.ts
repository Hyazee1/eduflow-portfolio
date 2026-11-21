import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import type { Data } from '../src/types';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const base = process.env.EDUFLOW_E2E_URL || 'http://127.0.0.1:8001';
const publicBase = process.env.EDUFLOW_E2E_PUBLIC_URL || 'http://127.0.0.1:8101';
const info = JSON.parse(fs.readFileSync(path.join(root, '.runtime/full-day-e2e-info.json'), 'utf8'));
const password = process.env.EDUFLOW_DEMO_PASSWORD || 'EduFlow-Demo-2026!';

test('full-day coverage, draft removal, empty state and confirmed publication', async ({ page }) => {
  test.setTimeout(420_000);
  expect(base).toBe('http://127.0.0.1:8001');
  expect(JSON.parse(fs.readFileSync(path.join(root, '.runtime/e2e-info.json'), 'utf8')).database).toBe('eduflow_ai_e2e_test');
  const request = async (route: string, token: string, body?: Data, origin = base) => {
    const response = await fetch(origin + '/api/v1' + route, {
      method: body ? 'POST' : 'GET',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token,
        ...(body ? { 'Idempotency-Key': crypto.randomUUID() } : {}) },
      body: body ? JSON.stringify(body) : undefined,
    });
    const value = await response.json();
    expect(response.ok, route + ': ' + JSON.stringify(value)).toBeTruthy();
    return value;
  };
  const login = async (username: string, role: string) => {
    const response = await fetch((role === 'ADMIN' ? base : publicBase) + '/api/v1/auth/login', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, role, password }),
    });
    expect(response.ok).toBeTruthy();
    return (await response.json()).access_token as string;
  };
  const token = await login('admin', 'ADMIN');
  // A failed prior attempt may leave an unpublished candidate in this dedicated test week.
  for (const row of (await request('/admin/versions?week=' + info.week + '&page_size=500', token)).items) {
    const candidate = await request('/admin/schedule/versions/' + row.id, token);
    expect(candidate.can_delete, 'Retain published or dependent test history; prepare a fresh isolated database').toBe(true);
    await request('/admin/schedule/versions/' + row.id + '/delete', token, { version: candidate.version });
  }
  const enrollments = async (week: string) => {
    const first = await request('/admin/demand?week=' + week + '&page_size=500', token);
    const items = [...first.items];
    for (let page = 2; items.length < first.total; page++) {
      items.push(...(await request('/admin/demand?week=' + week + '&page_size=500&page=' + page, token)).items);
    }
    return items;
  };
  const before = await enrollments(info.week);
  expect(before).toHaveLength(668);
  const oldWeekBefore = await enrollments(info.source_week);
  await page.goto(base + '/login');
  await page.getByLabel('账号', { exact: true }).fill('admin');
  await page.getByLabel('密码', { exact: true }).fill(password);
  await page.getByRole('button', { name: '登录工作台' }).click();
  await expect(page).toHaveURL(base + '/');
  await page.goto(base + '/scheduling?week=' + info.week);

  const generate = async (target: Page) => {
    const response = target.waitForResponse(r => r.url().endsWith('/admin/schedule/solve') && r.request().method() === 'POST');
    await target.getByRole('button', { name: /自动排课$/ }).click();
    const job = await (await response).json();
    let completed: Data = {};
    await expect.poll(async () => {
      completed = await request('/admin/schedule/jobs/' + job.id, token);
      expect(['FAILED', 'STALE']).not.toContain(completed.status);
      return completed.status;
    }, { timeout: 150_000, intervals: [1500] }).toBe('COMPLETED');
    expect(completed.version_id).toBeTruthy();
    const version = await request('/admin/schedule/versions/' + completed.version_id, token);
    expect(version.report.metrics).toMatchObject({ request_count: 668, fulfilled_count: 668, covered_slots: 40 });
    expect(version.report.hard_conflicts).toBe(0);
    expect(version.report.metrics.teacher_daily_rest.every((r: Data) => r.rest_slots >= 3)).toBeTruthy();
    await target.goto(base + '/scheduling?week=' + info.week + '&version=' + version.id);
    await expect(target.getByRole('button', { name: '删除草稿', exact: true })).toBeEnabled();
    return { version, job };
  };
  const first = await generate(page);
  const sole = first.version.lessons.find((s: Data) => s.slot_code !== 'S8'
    && first.version.lessons.filter((other: Data) => other.day === s.day && other.slot_code === s.slot_code).length === 1);
  expect(sole).toBeTruthy();
  const invalid = await fetch(base + '/api/v1/admin/schedule/versions/' + first.version.id + '/sessions', {
    method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token, 'Idempotency-Key': crypto.randomUUID() },
    body: JSON.stringify({ version: first.version.version, session_id: sole.id, teacher_id: sole.teacher_id,
      room_id: sole.room_id, day: sole.day, slot_code: sole.slot_code === 'S1' ? 'S2' : 'S1' }),
  });
  expect(invalid.status).toBe(422);
  expect((await invalid.json()).details.some((v: Data) => v.code === 'H23')).toBeTruthy();
  expect((await request('/admin/versions?week=' + info.week, token)).total).toBe(1);
  const validated = page.waitForResponse(r => r.url().endsWith('/validate'));
  await page.getByRole('button', { name: /独立校验$/ }).click();
  expect((await validated).ok()).toBeTruthy();
  await page.getByRole('tab', { name: '教师负载与休息', exact: true }).click();
  await expect(page.getByRole('columnheader', { name: '未授课时段' })).toBeVisible();
  await page.getByRole('button', { name: '删除草稿', exact: true }).click();
  await page.getByRole('button', { name: /^保\s*留$/ }).click();
  await expect(page.getByRole('dialog')).toBeHidden();
  await expect(page.getByRole('button', { name: '删除草稿', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '删除草稿', exact: true }).click();
  const removed = page.waitForResponse(r => r.url().endsWith('/delete'));
  await page.getByRole('dialog').getByRole('button', { name: '删除草稿', exact: true }).click();
  expect((await removed).ok()).toBeTruthy();
  await expect(page.getByRole('dialog')).toBeHidden();
  await expect(page.getByText('点击自动排课，生成第一份合法候选方案', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '删除草稿', exact: true })).toHaveCount(0);
  expect(page.url()).not.toContain(first.version.id);
  const detached = await request('/admin/schedule/jobs/' + first.job.id, token);
  expect(detached.version_id).toBeNull();
  expect(detached.result.candidate_deleted).toBeTruthy();
  const after = await enrollments(info.week);
  expect(after).toEqual(before);
  expect(await enrollments(info.source_week)).toEqual(oldWeekBefore);

  const second = await generate(page);
  await page.getByRole('button', { name: /校验并发布$/ }).click();
  const published = page.waitForResponse(r => r.url().endsWith('/publish'));
  await page.getByRole('button', { name: '发布并通知', exact: true }).click();
  expect((await (await published).json()).status).toBe('PUBLISHED_PENDING_CONFIRMATION');
  await expect(page.getByRole('button', { name: '取消此候选版本', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '删除草稿', exact: true })).toHaveCount(0);
  const pending = await request('/admin/schedule/versions/' + second.version.id, token);
  const rejected = await fetch(base + '/api/v1/admin/schedule/versions/' + pending.id + '/delete', {
    method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token, 'Idempotency-Key': crypto.randomUUID() },
    body: JSON.stringify({ version: pending.version }),
  });
  expect(rejected.status).toBe(409);
  const users = (await request('/admin/users?page_size=500', token)).items;
  const grouped = new Map<string, string[]>();
  for (const c of pending.confirmations.filter((c: Data) => c.decision === 'PENDING')) {
    grouped.set(c.user_id, [...(grouped.get(c.user_id) || []), c.logical_id]);
  }
  for (const [id, sessions] of grouped) {
    const user = users.find((u: Data) => u.id === id);
    const userToken = await login(user.username, user.role);
    await request('/schedule-confirmations', userToken, { version_id: pending.id, session_ids: sessions, decision: 'ACCEPTED' }, publicBase);
  }
  const active = await request('/admin/schedule/versions/' + pending.id, token);
  expect(active.status).toBe('ACTIVE');
  expect(active.report.metrics).toMatchObject({ covered_slots: 40, fulfilled_count: 668 });
  expect(active.report.hard_conflicts).toBe(0);
  await page.reload();
  await expect(page.getByRole('button', { name: '删除草稿', exact: true })).toHaveCount(0);
  fs.writeFileSync(path.join(root, '.runtime/private/full-day-20261101/browser-full-day.json'), JSON.stringify({
    week: info.week, solver_status: active.solver_status, metrics: active.report.metrics,
    deleted_candidate: first.version.id, preserved_enrollments: true, final_status: active.status,
    invalid_coverage_edit_rejected: true,
  }, null, 2));
});
