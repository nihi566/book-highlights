import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { mkdtempSync, readFileSync, existsSync, readdirSync, writeFileSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';
import { createStore } from '../cli/store.js';
import { createCompanionServer } from '../cli/server.js';
import { startFakeLlm } from './helpers/fake-llm.js';

const run = promisify(execFile);
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const BH = path.join(ROOT, 'cli/bh.js');
const fixture = (name) => path.join(ROOT, 'test/fixtures', name);
const tmp = (p) => mkdtempSync(path.join(tmpdir(), p));

async function withServer(fn, configure = {}) {
  const dataDir = tmp('bh-data-');
  const vault = tmp('bh-vault-');
  const fake = await startFakeLlm();
  const store = createStore(dataDir);
  await store.saveConfig({ vault, llm: { baseUrl: fake.url, chatModel: 'fake-chat', embedModel: 'fake-embed' }, allowedOrigins: ['https://example.github.io'], ...configure });
  const server = createCompanionServer({ store, log: () => {} });
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    await fn({ base, store, vault, fake, server });
  } finally {
    server.close();
    await fake.close();
  }
}

const b64 = async (file) => (await readFile(file)).toString('base64');

test('コンパニオンサーバ: 取り込み → 分析ジョブ → Vault 書き出し', async () => {
  await withServer(async ({ base, vault }) => {
    const info0 = await (await fetch(`${base}/api/info`)).json();
    assert.equal(info0.stats.highlights, 0);

    const imp = await fetch(`${base}/api/import`, {
      method: 'POST',
      body: JSON.stringify({ files: [{ name: 'My Clippings.txt', base64: await b64(fixture('My Clippings.txt')) }, { name: 'playbooks-ja.html', base64: await b64(fixture('playbooks-ja.html')) }] }),
    });
    const r = await imp.json();
    assert.equal(r.results.length, 2);
    assert.ok(r.stats.added >= 8);

    const start = await fetch(`${base}/api/analyze`, { method: 'POST', body: '{}' });
    assert.equal(start.status, 202);
    let job;
    for (let i = 0; i < 100; i++) {
      job = await (await fetch(`${base}/api/analyze`)).json();
      if (!job.running) break;
      await new Promise((res) => setTimeout(res, 50));
    }
    assert.equal(job.error, '');
    assert.equal(job.stage, 'done');
    assert.ok(job.vault.written > 3);
    const analysis = await (await fetch(`${base}/api/analysis`)).json();
    assert.ok(analysis.lines.length > 0);
    assert.ok(existsSync(path.join(vault, 'Highlights/Knowledge Map.md')));
    assert.ok(existsSync(path.join(vault, 'Highlights/Books/深い集中.md')));
    assert.ok(readdirSync(path.join(vault, 'Highlights/Lines')).length === analysis.lines.length);
  });
});

test('コンパニオンサーバ: CORS・Host・トークンの制限', async () => {
  await withServer(async ({ base }) => {
    const ok = await fetch(`${base}/api/info`, { headers: { Origin: 'https://example.github.io' } });
    assert.equal(ok.headers.get('access-control-allow-origin'), 'https://example.github.io');
    const pre = await fetch(`${base}/api/library`, { method: 'OPTIONS', headers: { Origin: 'https://example.github.io', 'Access-Control-Request-Private-Network': 'true' } });
    assert.equal(pre.status, 204);
    assert.equal(pre.headers.get('access-control-allow-private-network'), 'true');
    const evil = await fetch(`${base}/api/library`, { headers: { Origin: 'https://evil.example.com' } });
    assert.equal(evil.status, 403);
    const tsnet = await fetch(`${base}/api/info`, { headers: { Origin: 'https://my-pc.tail1234.ts.net' } });
    assert.equal(tsnet.status, 200);
  });
  await withServer(
    async ({ base }) => {
      assert.equal((await fetch(`${base}/api/info`)).status, 401);
      assert.equal((await fetch(`${base}/api/info`, { headers: { 'X-BH-Token': 'secret' } })).status, 200);
      assert.equal((await fetch(`${base}/`)).status, 200, '画面そのものはトークン不要');
    },
    { token: 'secret' },
  );
});

test('コンパニオンサーバ: LLM 中継・ライブラリ同期・静的ファイル', async () => {
  await withServer(async ({ base, fake }) => {
    const models = await (await fetch(`${base}/llm/v1/models`)).json();
    assert.deepEqual(models.data.map((m) => m.id), ['fake-chat', 'fake-embed']);
    const chat = await fetch(`${base}/llm/v1/chat/completions`, { method: 'POST', body: JSON.stringify({ model: 'fake-chat', messages: [{ role: 'user', content: '面の名前' }], response_format: { type: 'json_schema', json_schema: { name: 'plane' } } }) });
    assert.match(JSON.parse((await chat.json()).choices[0].message.content).name, /^テーマ/);
    assert.equal(fake.calls.chat, 1);

    const phone = { version: 1, books: { b1: { id: 'b1', title: 'スマホの本', author: '', sources: ['kindle'], updatedAt: '2025-01-01' } }, highlights: { h1: { id: 'h1', bookId: 'b1', source: 'kindle', text: 'スマホで取り込んだ点', updatedAt: '2025-01-01' } }, updatedAt: '2025-01-01' };
    const merged = await (await fetch(`${base}/api/library/merge`, { method: 'POST', body: JSON.stringify(phone) })).json();
    assert.equal(merged.highlights.h1.text, 'スマホで取り込んだ点');

    const page = await fetch(`${base}/`);
    assert.match(page.headers.get('content-type'), /text\/html/);
    const mod = await fetch(`${base}/core/model.js`);
    assert.match(mod.headers.get('content-type'), /javascript/);
    assert.equal((await fetch(`${base}/../package.json`)).status, 404);
  });
});

test('CLI: import → obsidian → 自分のメモが再出力で残る', async () => {
  const dataDir = tmp('bh-cli-');
  const vault = tmp('bh-cli-vault-');
  const env = { ...process.env, BH_DATA: dataDir };
  await run('node', [BH, 'config', 'vault', vault], { env });
  const out = await run('node', [BH, 'import', fixture('My Clippings.txt'), fixture('kindle-export-ja.html'), fixture('kindle-notebook.json'), '--obsidian'], { env });
  assert.match(out.stdout, /✓ My Clippings.txt: Kindle（My Clippings.txt）/);
  assert.match(out.stdout, /Obsidian: 書き込み \d+/);
  const note = path.join(vault, 'Highlights/Books/対人関係の地図.md');
  const content = readFileSync(note, 'utf8');
  assert.match(content, /上司との関係に使えそう/);
  writeFileSync(note, content + 'この本の感想を書いた\n');
  await run('node', [BH, 'obsidian'], { env });
  assert.match(readFileSync(note, 'utf8'), /この本の感想を書いた/);
  const list = await run('node', [BH, 'list'], { env });
  assert.match(list.stdout, /本 5 冊/);
  const search = await run('node', [BH, 'search', '信頼'], { env });
  assert.match(search.stdout, /1 件/);
  await assert.rejects(run('node', [BH, 'analyze'], { env }), /チャットモデルが未設定/);
});
