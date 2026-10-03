import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join, posix } from 'node:path';
import { fileURLToPath } from 'node:url';

const WEB = join(dirname(fileURLToPath(import.meta.url)), '..', 'web');

// sw.js の SHELL 配列を文字列のまま取り出す（sw.js は self 前提なので import できない）
function readShell() {
  const src = readFileSync(join(WEB, 'sw.js'), 'utf8');
  const body = src.match(/const SHELL = \[([\s\S]*?)\];/)[1];
  return new Set([...body.matchAll(/'([^']+)'/g)].map((m) => m[1]));
}

// web/ からの相対パスで、起動時に読み込まれる .js を静的 import と import('...') でたどる
function reachableModules(entry) {
  const seen = new Set();
  const stack = [entry];
  while (stack.length) {
    const rel = stack.pop();
    if (seen.has(rel)) continue;
    seen.add(rel);
    const src = readFileSync(join(WEB, rel), 'utf8');
    const specs = [
      ...[...src.matchAll(/^\s*(?:import|export)\s[^'"]*?from\s*['"]([^'"]+)['"]/gm)].map((m) => m[1]),
      ...[...src.matchAll(/^\s*import\s*['"]([^'"]+)['"]/gm)].map((m) => m[1]),
      ...[...src.matchAll(/import\(\s*['"]([^'"]+)['"]\s*\)/g)].map((m) => m[1]),
    ];
    for (const spec of specs) {
      if (!spec.startsWith('.')) continue;
      stack.push(posix.normalize(posix.join(posix.dirname(rel), spec)));
    }
  }
  return [...seen];
}

test('サービスワーカー: 起動時に読み込むすべての .js が事前キャッシュに入っている', () => {
  const html = readFileSync(join(WEB, 'index.html'), 'utf8');
  const entries = [...html.matchAll(/<script[^>]*type="module"[^>]*src="([^"]+)"/g)].map((m) => m[1]);
  assert.ok(entries.length > 0, 'index.html にモジュールの script がある');
  const shell = readShell();
  const modules = entries.flatMap(reachableModules);
  assert.ok(modules.includes('core/kindle-status.js'), 'たどり方の確認: ui.js 経由の kindle-status.js に届く');
  const missing = modules.filter((m) => !shell.has(m));
  assert.deepEqual(missing, [], `sw.js の SHELL に足りない: ${missing.join(', ')}`);
});

test('サービスワーカー: SHELL のファイルはすべて実在する', () => {
  for (const p of readShell()) {
    if (p === './') continue;
    assert.doesNotThrow(() => readFileSync(join(WEB, p)), `${p} が web/ に無い`);
  }
});
