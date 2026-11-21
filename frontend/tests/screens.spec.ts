import { test, expect } from '@playwright/test';

const roles = [
  { role: '管理员', username: 'admin', routes: ['/', '/students', '/teachers', '/catalog', '/rooms', '/demand', '/offering-reviews', '/scheduling', '/schedule', '/approvals', '/changes', '/analytics', '/rules', '/audit', '/notifications'] },
  { role: '教师', username: 'teacher01', routes: ['/', '/schedule', '/reviews', '/classes', '/requests', '/changes', '/profile', '/notifications'] },
  { role: '学生', username: 'student001', routes: ['/', '/schedule', '/courses', '/applications', '/changes', '/profile', '/notifications'] },
];

for (const user of roles) {
  test(user.role + ' pages read real API data without browser errors', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    const origin = user.username === 'admin' ? 'http://127.0.0.1:8001' : 'http://127.0.0.1:8101';
    await page.goto(origin + '/login');
    await page.getByText(user.role, { exact: true }).click();
    await page.getByLabel('账号', { exact: true }).fill(user.username);
    await page.getByLabel('密码', { exact: true }).fill(process.env.EDUFLOW_DEMO_PASSWORD || 'EduFlow-Demo-2026!');
    await page.getByRole('button', { name: '登录工作台' }).click();
    await expect(page).toHaveURL(/\/$/);
    for (const route of user.routes) {
      await test.step(route, async () => {
        await page.goto(origin + route);
        await expect(page.locator('.page-title h1')).toBeVisible();
        await expect(page.getByText(/暂不可用|无法读取|数据读取失败|页面不存在/)).toHaveCount(0);
      });
    }
    expect(errors).toEqual([]);
  });
}
