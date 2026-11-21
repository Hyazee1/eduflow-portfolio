import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
assert.deepEqual(fs.readdirSync(path.join(root, 'dist')).sort(), ['admin', 'public'], 'Obsolete single-portal output must be removed');
const publicAssets = path.join(root, 'dist/public/assets');
const files = fs.readdirSync(publicAssets);
const javascript = files.filter(name => name.endsWith('.js')).map(name => fs.readFileSync(path.join(publicAssets, name), 'utf8')).join('\n');
for (const marker of ['/admin/', '邀请教师审核', '开课审核管理', 'AdminAccounts', 'AdminWorkspace']) {
  assert(!javascript.includes(marker), 'Public bundle contains a management dependency: ' + marker);
}
assert(!files.some(name => name.endsWith('.map')), 'Source maps must remain disabled');
assert(!files.some(name => /^(charts|AdminWorkspace|AdminAccounts|Dashboard)-/.test(name)), 'Public assets contain an admin module');
for (const name of ['index.html', 'assets']) {
  assert(fs.existsSync(path.join(root, 'dist/admin', name)), 'Admin build is missing: ' + name);
  assert(fs.existsSync(path.join(root, 'dist/public', name)), 'Public build is missing: ' + name);
}
console.log('PASS: two portal builds; public assets exclude management endpoints, pages, charts and source maps.');
