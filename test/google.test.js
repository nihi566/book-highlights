import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { execFile } from 'node:child_process';
import { mkdtempSync, readFileSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';
import { createStore } from '../cli/store.js';
import { createCompanionServer } from '../cli/server.js';
import { createGoogleClient, startDriveWatcher } from '../cli/google.js';
import { deleteBook } from '../web/core/model.js';

const run = promisify(execFile);
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const BH = path.join(ROOT, 'cli/bh.js');
const tmp = (p) => mkdtempSync(path.join(tmpdir(), p));
const PLAYBOOKS_HTML = readFileSync(path.join(ROOT, 'test/fixtures/playbooks-ja.html'));
const FOLDER_ID = 'folder-play-books-notes';

const json = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });

/** Google の OAuth / Drive API の偽物。docs を書き換えると「Play ブックスがドキュメントを更新した」ことになる */
function fakeGoogle({ docs = [], folders = [{ id: FOLDER_ID, name: 'Play ブックスのメモ' }], tokenError = null } = {}) {
  const calls = { token: [], exports: [], revoke: 0, list: 0 };
  let n = 0;
  const fetchImpl = async (url, init = {}) => {
    const u = new URL(url);
    if (u.href.startsWith('https://oauth2.googleapis.com/token')) {
      const body = Object.fromEntries(new URLSearchParams(String(init.body)));
      calls.token.push(body);
      if (tokenError) return json({ error: tokenError, error_description: 'Token has been expired or revoked.' }, 400);
      return json({ access_token: `at-${++n}`, expires_in: 3599, refresh_token: body.grant_type === 'authorization_code' ? 'rt-1' : undefined, scope: 'https://www.googleapis.com/auth/drive.readonly' });
    }
    if (u.href.startsWith('https://oauth2.googleapis.com/revoke')) {
      calls.revoke++;
      return json({});
    }
    assert.match(init.headers?.Authorization || '', /^Bearer at-\d+$/);
    if (u.pathname === '/drive/v3/files') {
      const query = u.searchParams.get('q');
      if (query.includes('vnd.google-apps.folder')) return json({ files: folders });
      calls.list++;
      assert.match(query, new RegExp(`'${FOLDER_ID}' in parents`));
      return json({ files: docs.map(({ id, name, modifiedTime }) => ({ id, name, modifiedTime })) });
    }
    const m = u.pathname.match(/^\/drive\/v3\/files\/([^/]+)\/export$/);
    if (m && u.searchParams.get('mimeType') === 'text/html') {
      calls.exports.push(m[1]);
      const doc = docs.find((d) => d.id === m[1]);
      return new Response(doc.body, { headers: { 'Content-Type': 'text/html' } });
    }
    return json({ error: { message: `unexpected ${u.href}` } }, 404);
  };
  return { fetchImpl, calls };
}

async function setup({ loggedIn = true, vault = false, ...opts } = {}) {
  const store = createStore(tmp('bh-google-'));
  const vaultDir = vault ? tmp('bh-google-vault-') : '';
  await store.saveConfig({ vault: vaultDir, google: { clientId: 'cid.apps.googleusercontent.com', clientSecret: 'secret' } });
  if (loggedIn) await store.saveGoogleToken({ refreshToken: 'rt-1' });
  const google = fakeGoogle(opts);
  return { store, google, vaultDir, client: createGoogleClient({ store, fetchImpl: google.fetchImpl }) };
}

const doc = (modifiedTime, body = PLAYBOOKS_HTML) => ({ id: 'doc1', name: '「深い集中」のメモ', modifiedTime, body });

test('Google ドライブ: 更新されたドキュメントだけを書き出して取り込み、Vault にも書く', async () => {
  const docs = [doc('2026-09-27T01:00:00.000Z')];
  const { store, google, client, vaultDir } = await setup({ docs, vault: true });

  const first = await client.sync();
  assert.equal(first.changed, 1);
  assert.ok(first.added > 0, '新しい点が入る');
  assert.equal(first.errors.length, 0);
  assert.ok(first.vault.written > 0, 'Vault に書き出す');
  const lib = await store.library();
  assert.ok(Object.values(lib.books).some((b) => b.title === '深い集中'));
  assert.ok(readdirSync(path.join(vaultDir, 'Highlights', 'Books')).length > 0);

  // ドキュメントが変わっていなければ書き出さない
  const second = await client.sync();
  assert.equal(second.changed, 0);
  assert.equal(google.calls.exports.length, 1);

  // Play ブックスでドキュメントが更新された → もう一度書き出す（同じ点は重複しない）
  docs[0] = doc('2026-09-27T01:05:00.000Z');
  const third = await client.sync();
  assert.equal(third.changed, 1);
  assert.equal(third.added, 0);
  assert.equal(google.calls.exports.length, 2);

  // アクセストークンは使い回す（毎回リフレッシュしない）
  assert.equal(google.calls.token.length, 1);
  assert.equal(google.calls.token[0].grant_type, 'refresh_token');
});

test('Google ドライブ: 読めないドキュメントはエラーとして返し、他の取り込みは続ける', async () => {
  const docs = [doc('2026-09-27T01:00:00.000Z'), { id: 'doc2', name: '関係ない文書', modifiedTime: '2026-09-27T01:00:00.000Z', body: '<html><body><p>ただのメモ</p></body></html>' }];
  const { client, google } = await setup({ docs });
  const r = await client.sync();
  assert.ok(r.added > 0);
  assert.equal(r.errors.length, 1);
  assert.match(r.errors[0], /関係ない文書/);
  // 中身が変わるまでは試し直さない
  assert.equal((await client.sync()).changed, 0);
  assert.equal(google.calls.exports.length, 2);
});

test('Google ドライブ: アプリで削除した本は、自動取り込みでは戻さない', async () => {
  const docs = [doc('2026-09-27T01:00:00.000Z')];
  const { store, client } = await setup({ docs });
  await client.sync();
  const lib = await store.library();
  const bookId = Object.keys(lib.books)[0];
  deleteBook(lib, bookId);
  await store.saveLibrary(lib);

  // 削除した本に Play ブックスで線を 1 本足した（ドキュメントが更新された）
  const withNewLine = PLAYBOOKS_HTML.toString('utf8').replaceAll('休息は仕事の反対ではなく、次の集中のための準備である。', '休息は次の集中のための準備である（新しく引いた線）。');
  docs[0] = doc('2026-09-27T01:05:00.000Z', withNewLine);
  const r = await client.sync();
  assert.equal(r.changed, 1);
  assert.equal(r.added, 0);
  const after = await store.library();
  assert.equal(after.books[bookId].deleted, true);
  assert.ok(Object.values(after.highlights).filter((h) => h.bookId === bookId).every((h) => h.deleted));
});

test('Google ドライブ: フォルダが空になったら探し直し、取り込み済みの記録は消さない', async () => {
  const docs = [doc('2026-09-27T01:00:00.000Z')];
  const { store, client, google } = await setup({ docs });
  await client.sync();
  docs.length = 0;
  assert.equal((await client.sync()).checked, 0);
  assert.ok((await store.googleSync()).files.doc1, '記録は残る');
  docs.push(doc('2026-09-27T01:00:00.000Z'));
  assert.equal((await client.sync()).changed, 0, '同じドキュメントは取り込み直さない');
  assert.equal(google.calls.exports.length, 1);
});

test('Google ドライブ: 未ログイン・トークン失効はログインし直しを求める', async () => {
  const notLoggedIn = await setup({ loggedIn: false });
  await assert.rejects(notLoggedIn.client.sync(), (e) => e.needsLogin && /bh google login/.test(e.message));

  const expired = await setup({ tokenError: 'invalid_grant' });
  await assert.rejects(expired.client.sync(), (e) => e.needsLogin && /ログインし直して/.test(e.message));
});

test('Google ドライブ: フォルダが見つからなければ設定方法を案内する', async () => {
  const { client } = await setup({ folders: [] });
  await assert.rejects(client.sync(), /Play ブックスのメモ.*google-folder/s);
});

test('Google ログイン: ループバックで code を受け取り、PKCE で交換してリフレッシュトークンを保存する', async () => {
  const { store, google, client } = await setup({ loggedIn: false, docs: [] });
  let authUrl;
  await client.login({
    log: () => {},
    openBrowser: (url) => {
      authUrl = new URL(url);
      // ブラウザの代わりに、Google からの戻りを真似る
      const back = new URL(authUrl.searchParams.get('redirect_uri'));
      back.searchParams.set('code', 'auth-code');
      back.searchParams.set('state', authUrl.searchParams.get('state'));
      fetch(back).catch(() => {});
    },
  });
  assert.equal(authUrl.searchParams.get('scope'), 'https://www.googleapis.com/auth/drive.readonly');
  assert.equal(authUrl.searchParams.get('access_type'), 'offline');
  assert.match(authUrl.searchParams.get('redirect_uri'), /^http:\/\/127\.0\.0\.1:\d+$/);
  const exchange = google.calls.token[0];
  assert.equal(exchange.code, 'auth-code');
  const challenge = createHash('sha256').update(exchange.code_verifier).digest('base64url');
  assert.equal(challenge, authUrl.searchParams.get('code_challenge'));
  assert.equal((await store.googleToken()).refreshToken, 'rt-1');

  await client.logout();
  assert.equal(google.calls.revoke, 1);
  assert.equal(await store.googleToken(), null);
});

test('Google ログイン: state が違う戻りは受け付けない', async () => {
  const { client, store } = await setup({ loggedIn: false });
  await assert.rejects(
    client.login({
      log: () => {},
      openBrowser: (url) => {
        const back = new URL(new URL(url).searchParams.get('redirect_uri'));
        back.searchParams.set('code', 'auth-code');
        back.searchParams.set('state', 'forged');
        fetch(back).catch(() => {});
      },
    }),
    /state/,
  );
  assert.equal(await store.googleToken(), null);
});

test('bh serve の見張り役: 取り込み状況を /api/info で返す', async () => {
  const { store, client } = await setup({ docs: [doc('2026-09-27T01:00:00.000Z')] });
  const drive = startDriveWatcher({ store, client, log: () => {} });
  const server = createCompanionServer({ store, log: () => {}, drive });
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  try {
    for (let i = 0; i < 50 && !drive.status.lastCheck; i++) await new Promise((r) => setTimeout(r, 20));
    const info = await (await fetch(`http://127.0.0.1:${server.address().port}/api/info`)).json();
    assert.equal(info.google.active, true);
    assert.ok(info.google.lastImport);
    assert.ok(info.updatedAt, 'Web アプリが新しさを比べるための更新日時');
    assert.ok(info.stats.highlights > 0);
  } finally {
    drive.stop();
    server.close();
  }
});

test('bh config: Google のクライアント シークレットは表示しない・フォルダ ID を検証する', async () => {
  const dataDir = tmp('bh-google-cli-');
  const bh = (...args) => run('node', [BH, ...args], { env: { ...process.env, BH_DATA: dataDir } });
  await bh('config', 'google-client', 'cid.apps.googleusercontent.com', 'very-secret');
  const { stdout } = await bh('config');
  assert.match(stdout, /cid\.apps\.googleusercontent\.com/);
  assert.doesNotMatch(stdout, /very-secret/);
  await assert.rejects(bh('config', 'google-folder', "x' or name != '"), /フォルダ ID/);
  await bh('config', 'google-folder', '1AbCdEfGhIjKlMnOp_qr-st');
  await assert.rejects(bh('config', 'google-interval', '3'), /15 秒以上/);
});
