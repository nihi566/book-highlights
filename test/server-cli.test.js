import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import http from 'node:http';
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
  // 書誌 DB には実際に接続しない（テストがネットワークに依存しないように）
  const catalogFetch = async () => new Response(JSON.stringify({ items: [] }));
  const server = createCompanionServer({ store, log: () => {}, catalogFetch });
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

/** fetch では Host を変えられないので http.request で送る */
function statusWith(base, headers) {
  const u = new URL(`${base}/api/info`);
  return new Promise((resolve, reject) => {
    const req = http.request({ host: u.hostname, port: u.port, path: u.pathname, headers }, (res) => {
      res.resume();
      resolve(res.statusCode);
    });
    req.on('error', reject);
    req.end();
  });
}

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

test('コンパニオンサーバ: ブラウザ拡張からの自動取り込み（auto: true）は削除した本を復活させない', async () => {
  await withServer(async ({ base, store }) => {
    const notebook = (texts) => ({
      files: [
        {
          name: 'kindle-auto.json',
          base64: Buffer.from(JSON.stringify({ format: 'book-highlights/kindle-notebook', version: 1, books: [{ asin: 'B000TEST', title: '自動の本', author: '著者', highlights: texts.map((text) => ({ text })) }] })).toString('base64'),
        },
      ],
      auto: true,
    });
    const post = async (body) => (await fetch(`${base}/api/import`, { method: 'POST', body: JSON.stringify(body) })).json();
    const r1 = await post(notebook(['点A']));
    assert.equal(r1.stats.added, 1);
    // 同じ内容を何度送っても増えない（拡張は変化のあった本を丸ごと送る）
    const r2 = await post(notebook(['点A', '点B']));
    assert.equal(r2.stats.added, 1);
    assert.equal(r2.stats.unchanged, 1);

    const lib = await store.library();
    const book = Object.values(lib.books).find((b) => b.title === '自動の本');
    book.deleted = true;
    await store.saveLibrary(lib);
    const r3 = await post(notebook(['点A', '点B', '点C']));
    assert.equal(r3.stats.skippedDeletedBooks, 1);
    assert.ok((await store.library()).books[book.id].deleted);
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
    // Tailscale Serve 経由: 画面と同じ ts.net からのリクエストは通し、他の ts.net（公開 Funnel など）は拒否
    assert.equal(await statusWith(base, { Origin: 'https://my-pc.tail1234.ts.net', Host: 'my-pc.tail1234.ts.net' }), 200);
    assert.equal(await statusWith(base, { Origin: 'https://evil.tail9999.ts.net', Host: 'my-pc.tail1234.ts.net' }), 403);
    assert.equal(await statusWith(base, { Host: 'evil.example.com' }), 403, 'DNS リバインディング対策');
    assert.equal((await fetch(`${base}/%E0%A4%A`)).status, 400);
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

test('コンパニオンサーバ: 拡張の確認結果を記録し /api/info で返す', async () => {
  await withServer(async ({ base, store }) => {
    const post = (body, headers = {}) => fetch(`${base}/api/kindle-status`, { method: 'POST', headers, body: JSON.stringify(body) });
    const info = async () => (await fetch(`${base}/api/info`)).json();
    assert.equal((await info()).kindleSync, null);

    // 既存の lastExport を消さない
    await store.saveState({ lastExport: { at: '2026-10-01T00:00:00.000Z', trigger: 'manual' } });
    const r = await post({ ok: true, added: 5, intervalMin: 15, token: 'leak' });
    assert.equal(r.status, 200);
    assert.deepEqual(await r.json(), { ok: true });
    const i1 = await info();
    assert.deepEqual({ ok: i1.kindleSync.lastCheck.ok, added: i1.kindleSync.lastCheck.added, intervalMin: i1.kindleSync.lastCheck.intervalMin, error: i1.kindleSync.lastCheck.error, needLogin: i1.kindleSync.lastCheck.needLogin }, { ok: true, added: 5, intervalMin: 15, error: '', needLogin: false });
    assert.equal(i1.kindleSync.lastNew.added, 5);
    assert.equal(i1.lastExport.trigger, 'manual');
    assert.equal(JSON.stringify(await store.state()).includes('leak'), false);

    // 失敗の報告のあとも「最後に新しい点」は残る
    await post({ ok: false, added: 0, error: '読めません' });
    const i2 = await info();
    assert.equal(i2.kindleSync.lastCheck.ok, false);
    assert.equal(i2.kindleSync.lastCheck.error, '読めません');
    assert.equal(i2.kindleSync.lastNew.added, 5);
    assert.ok(i2.kindleSync.lastSuccessAt);

    // 不正な本文は 400 で、保存内容は変わらない
    const before = await store.state();
    const bad = await post({ ok: 'yes' });
    assert.equal(bad.status, 400);
    assert.ok((await bad.json()).error);
    assert.deepEqual(await store.state(), before);
  });
});

test('コンパニオンサーバ: 確認結果の記録もトークンと Origin の制限を受ける', async () => {
  await withServer(
    async ({ base, store }) => {
      const send = (headers) => fetch(`${base}/api/kindle-status`, { method: 'POST', headers, body: JSON.stringify({ ok: true }) });
      assert.equal((await send({})).status, 401);
      assert.equal((await send({ 'X-BH-Token': 'secret', Origin: 'https://evil.example.com' })).status, 403);
      assert.equal((await send({ 'X-BH-Token': 'secret' })).status, 200);
      assert.ok((await store.state()).kindleSync);
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

test('autoexport off: PC の分析が完了しても Vault に書き出さない（サーバ）', async () => {
  await withServer(
    async ({ base, vault }) => {
      await fetch(`${base}/api/import`, {
        method: 'POST',
        body: JSON.stringify({ files: [{ name: 'My Clippings.txt', base64: await b64(fixture('My Clippings.txt')) }, { name: 'playbooks-ja.html', base64: await b64(fixture('playbooks-ja.html')) }] }),
      });
      const start = await fetch(`${base}/api/analyze`, { method: 'POST', body: '{}' });
      assert.equal(start.status, 202);
      let job;
      for (let i = 0; i < 100; i++) {
        job = await (await fetch(`${base}/api/analyze`)).json();
        if (!job.running) break;
        await new Promise((res) => setTimeout(res, 50));
      }
      assert.equal(job.stage, 'done');
      assert.equal(job.vault, null);
      assert.deepEqual(readdirSync(vault), []);
      assert.equal((await (await fetch(`${base}/api/info`)).json()).lastExport, null);
    },
    { autoExport: false },
  );
});

test('autoexport off: bh analyze のあとも Vault に書き出さない（CLI）', async () => {
  const dataDir = tmp('bh-cli-off-');
  const vault = tmp('bh-cli-off-vault-');
  const fake = await startFakeLlm();
  try {
    await createStore(dataDir).saveConfig({ vault, autoExport: false, llm: { baseUrl: fake.url, chatModel: 'fake-chat', embedModel: 'fake-embed' } });
    const env = { ...process.env, BH_DATA: dataDir };
    await run('node', [BH, 'import', fixture('My Clippings.txt'), fixture('playbooks-ja.html')], { env });
    const out = await run('node', [BH, 'analyze', '--no-recommend'], { env });
    assert.doesNotMatch(out.stdout, /Obsidian: 書き込み/);
    assert.deepEqual(readdirSync(vault), []);
  } finally {
    await fake.close();
  }
});
