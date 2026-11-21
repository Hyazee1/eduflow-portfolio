import { test, expect } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const evidence = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../.runtime/private/evidence');

for (const person of [{ username: 'student001', role: '学生', label: '课程选课', route: '/courses' },
  { username: 'teacher01', role: '教师', label: '开课审核', route: '/reviews' }]) {
  test(person.role + ' touch navigation and day schedule under device emulation', async ({ page, browserName }) => {
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => {
      window.addEventListener('touchstart', () => {
        const root = document.documentElement;
        root.dataset.observedTouches = String(Number(root.dataset.observedTouches || 0) + 1);
      }, { passive: true });
    });
    await page.goto('http://127.0.0.1:8101/login');
    await expect(page.getByRole('button', { name: '填入演示账号' })).toBeVisible();
    await page.getByText(person.role, { exact: true }).tap();
    await page.getByLabel('账号', { exact: true }).fill(person.username);
    await page.getByLabel('密码', { exact: true }).fill(process.env.EDUFLOW_DEMO_PASSWORD || 'EduFlow-Demo-2026!');
    await page.getByRole('button', { name: '登录工作台' }).tap();
    await expect(page).toHaveURL('http://127.0.0.1:8101/');
    await page.getByRole('button', { name: '打开导航' }).tap();
    let drawer = page.locator('.ant-drawer-content');
    await drawer.getByRole('link', { name: '本周课表', exact: true }).tap();
    await expect(drawer).toBeHidden();
    await expect(page).toHaveURL('http://127.0.0.1:8101/schedule');
    await expect(page.locator('.page-title h1')).toBeVisible();
    await page.getByRole('button', { name: '打开导航' }).tap();
    drawer = page.locator('.ant-drawer-content');
    await drawer.getByRole('link', { name: person.label, exact: true }).tap();
    await expect(page).toHaveURL('http://127.0.0.1:8101' + person.route);
    await expect(page.locator('.page-title h1')).toBeVisible();
    expect(await page.evaluate(() => Number(document.documentElement.dataset.observedTouches || 0))).toBeGreaterThan(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(2);
    await page.screenshot({ path: path.join(evidence, browserName + '-device-' + person.username + '.png'), fullPage: true });
    expect(errors).toEqual([]);
  });
}
