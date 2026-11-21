import { test, expect } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const evidence = path.join(root, '.runtime/private/evidence');
const people = [
  { role: '学生', username: 'student001', route: '/courses', label: '课程选课', origin: 'http://127.0.0.1:8101' },
  { role: '教师', username: 'teacher01', route: '/reviews', label: '开课审核', origin: 'http://127.0.0.1:8101' },
  { role: '管理员', username: 'admin', route: '/students', label: '学生 CRM', origin: 'http://127.0.0.1:8001' },
];

for (const person of people) {
  test(person.role + ' responsive portal works at five widths', async ({ page, browserName }) => {
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.goto(person.origin + '/login');
    await expect(page.getByRole('button', { name: '填入演示账号' })).toBeVisible();
    await page.getByText(person.role, { exact: true }).click();
    await page.getByLabel('账号', { exact: true }).fill(person.username);
    await page.getByLabel('密码', { exact: true }).fill(process.env.EDUFLOW_DEMO_PASSWORD || 'EduFlow-Demo-2026!');
    await page.getByLabel('密码', { exact: true }).press('Enter');
    await expect(page).toHaveURL(person.origin + '/');
    for (const width of [360, 390, 430, 768, 1440]) {
      await test.step('viewport ' + width, async () => {
        await page.setViewportSize({ width, height: 900 });
        async function navigate(label: string) {
          if (width < 768) {
            await page.getByRole('button', { name: '打开导航' }).click();
            const drawer = page.locator('.ant-drawer-content');
            await expect(drawer).toBeVisible();
            await drawer.getByRole('link', { name: label, exact: true }).click();
            await expect(drawer).toBeHidden();
          } else {
            await page.locator('.ant-layout-sider').getByRole('link', { name: label, exact: true }).click();
          }
        }
        await navigate(person.role === '管理员' ? '全机构课表' : '本周课表');
        await expect(page).toHaveURL(person.origin + '/schedule');
        await expect(page.locator('.page-title h1')).toBeVisible();
        await navigate(person.label);
        await expect(page).toHaveURL(person.origin + person.route);
        await expect(page.locator('.page-title h1')).toBeVisible();
        await expect(page.getByText(/暂不可用|无法读取|数据读取失败/)).toHaveCount(0);
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
        expect(overflow, 'page must fit viewport; tables may scroll internally').toBeLessThanOrEqual(2);
        await page.screenshot({ path: path.join(evidence, browserName + '-' + person.username + '-' + width + '.png'), fullPage: true });
      });
    }
    expect(errors).toEqual([]);
  });
}
