import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  testDir: './tests',
  timeout: 360_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  outputDir: path.resolve(root, '../.runtime/private/browser-output', process.env.EDUFLOW_EVIDENCE_RUN || new Date().toISOString().replace(/[:.]/g, '-')),
  reporter: [['list'], ['json', { outputFile: process.env.EDUFLOW_PLAYWRIGHT_REPORT || path.resolve(root, '../.runtime/private/evidence/playwright-results.json') }], ['html', { open: 'never', outputFolder: path.resolve(root, '../.runtime/private/playwright-report') }]],
  use: {
    actionTimeout: 20_000,
    navigationTimeout: 30_000,
    baseURL: process.env.EDUFLOW_E2E_URL || 'http://127.0.0.1:8001',
    viewport: { width: 1440, height: 1000 },
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'chromium', testIgnore: '**/mobile.spec.ts', use: { browserName: 'chromium' } },
    { name: 'firefox', testMatch: '**/compatibility.spec.ts', use: { browserName: 'firefox' } },
    { name: 'webkit', testMatch: '**/compatibility.spec.ts', use: { browserName: 'webkit' } },
    { name: 'chromium-mobile', testMatch: '**/mobile.spec.ts', use: { ...devices['Pixel 7'], browserName: 'chromium' } },
    { name: 'webkit-mobile', testMatch: '**/mobile.spec.ts', use: { ...devices['iPhone 13'], browserName: 'webkit' } },
  ],
});
