// 課題発見（2026-09-27）で見つかった不具合の再現テスト
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { bookHighlights, bookIdFor, deleteBook, emptyLibrary, highlightIdFor, listBooks, mergeLibraries, mergeParsed, updateHighlight } from '../web/core/model.js';
import { mergeManaged, planVaultWrite, renderVault } from '../web/core/obsidian.js';
import { SAMPLE_BOOKS } from '../web/core/sample.js';

const T1 = '2025-01-01T00:00:00.000Z';

function sampleLibrary() {
  const lib = emptyLibrary();
  mergeParsed(lib, SAMPLE_BOOKS, { now: T1 });
  return lib;
}

function bookNote(lib, title = '小さな習慣の力') {
  return renderVault(lib, null).find((f) => f.path === `Highlights/Books/${title}.md`).content;
}

// ---- 20260927-obsidian-spaced-property-lost ----

test('空白を含む名前のプロパティ（date read）は書き出し直しても残る', () => {
  const lib = sampleLibrary();
  const generated = bookNote(lib);
  const edited = generated.replace('tags: ["book-highlights/book"]\n', 'tags: ["book-highlights/book"]\ndate read: 2024-05-01\n"my rating": 5\n');
  const merged = mergeManaged(edited, generated);
  assert.match(merged, /^date read: 2024-05-01$/m);
  assert.match(merged, /^"my rating": 5$/m);
  assert.equal(merged.match(/^date read:/gm).length, 1);
  // もう一度書き出しても増えも減りもしない
  assert.equal(mergeManaged(merged, generated), merged);
});

test('生成キーの直後に足した複数行のプロパティ・コメントも残る', () => {
  const lib = sampleLibrary();
  const generated = bookNote(lib);
  const edited = generated.replace('tags: ["book-highlights/book"]\n', 'tags: ["book-highlights/book"]\nread with:\n  - 友人A\n  - 友人B\n# 自分用のコメント\n');
  const merged = mergeManaged(edited, generated);
  assert.match(merged, /^read with:\n {2}- 友人A\n {2}- 友人B$/m);
  assert.match(merged, /^# 自分用のコメント$/m);
});

test('tags に自分で足したタグは消えない（生成タグと合わせる）', () => {
  const lib = sampleLibrary();
  const generated = bookNote(lib);
  const inline = mergeManaged(generated.replace('tags: ["book-highlights/book"]', 'tags: ["book-highlights/book", "名著"]'), generated);
  assert.match(inline, /^tags: \["book-highlights\/book", "名著"\]$/m);
  const block = mergeManaged(generated.replace('tags: ["book-highlights/book"]', 'tags:\n  - 再読したい\n  - book-highlights/book'), generated);
  assert.match(block, /^tags: \["book-highlights\/book", "再読したい"\]$/m);
});

test('空白入りのプロパティを足したノートは、分析から外れても削除しない', async () => {
  const lib = sampleLibrary();
  const hs = Object.values(lib.highlights);
  const analysis = {
    createdAt: T1,
    model: { chat: 'x' },
    stats: { points: 2 },
    lines: [{ id: 'l1', name: '線A', summary: 's', insight: '', keywords: [], highlightIds: [hs[0].id, hs[1].id], bookIds: [] }],
    planes: [{ id: 'p1', name: '面A', summary: 's', lineIds: ['l1'] }],
    solid: { title: 't', core: 'c', relations: [], principles: [], questions: [] },
    isolated: [],
    recommendations: [],
  };
  const disk = new Map();
  const read = async (p) => disk.get(p) ?? null;
  for (const w of (await planVaultWrite(renderVault(lib, analysis), read)).writes) disk.set(w.path, w.content);
  const linePath = 'Highlights/Lines/線A.md';
  disk.set(linePath, disk.get(linePath).replace('tags:', 'date read: 2024-05-01\ntags:'));
  const plan = await planVaultWrite(renderVault(lib, null), read);
  assert.ok(!plan.deletes.includes(linePath), '削除されない');
  assert.ok(plan.orphaned.includes(linePath), '自分の書き込みがあるノートとして残す');
});

// ---- 20260927-crlf-note-duplicate-frontmatter ----

test('改行コードが CRLF のノートでも frontmatter が二重にならず、CRLF のまま保たれる', async () => {
  const lib = sampleLibrary();
  const generated = bookNote(lib);
  const crlf = generated.replace('\n## 自分のメモ\n', '\n## 自分のメモ\n\n読み終えた\n').replace(/\n/g, '\r\n');
  const merged = mergeManaged(crlf, generated.replace('行動を変えたいなら', '行動を変えたいなら（更新）'));
  assert.equal(merged.match(/^---\r?$/gm).length, 2, 'frontmatter の区切りは 2 本だけ');
  assert.equal(merged.match(/^title:/gm).length, 1);
  assert.ok(merged.includes('（更新）'));
  assert.ok(merged.includes('読み終えた'));
  assert.ok(!/[^\r]\n/.test(merged), '改行はすべて CRLF のまま');
  // 変化が無ければ書き込まない
  const disk = new Map([['Highlights/Books/小さな習慣の力.md', mergeManaged(crlf, generated)]]);
  const plan = await planVaultWrite([{ path: 'Highlights/Books/小さな習慣の力.md', content: generated }], async (p) => disk.get(p) ?? null);
  assert.equal(plan.unchanged, 1);
});

test('BOM 付きのノートでも frontmatter を正しく読む', () => {
  const lib = sampleLibrary();
  const generated = bookNote(lib);
  const merged = mergeManaged('﻿' + generated.replace('tags:', 'date read: 2024-05-01\ntags:'), generated);
  assert.equal(merged.match(/^﻿?---$/gm).length, 2);
  assert.match(merged, /^date read: 2024-05-01$/m);
});

// ---- 20260927-sync-overwrites-unsynced-phone-edits ----

function twoDevices() {
  const pc = emptyLibrary();
  mergeParsed(pc, [{ title: '本', author: '著者', source: 'kindle', highlights: [{ text: '点A', location: 10 }, { text: '点B', location: 20 }] }], { now: '2025-01-01T00:00:00.000Z' });
  const phone = structuredClone(pc);
  return { pc, phone, id: highlightIdFor(bookIdFor('本'), '点A') };
}

test('スマホで付けた★・メモは、PC で別形式を取り込んで空欄が埋まった後の同期でも消えない', () => {
  const { pc, phone, id } = twoDevices();
  // スマホ: 未同期のまま★とメモ（10:00）
  updateHighlight(phone, id, { favorite: true, userNote: 'スマホのメモ', tags: ['大事'] }, '2025-02-01T10:00:00.000Z');
  // PC: 別形式（エクスポート HTML）の取り込みで章・色が埋まる（11:00）
  const s = mergeParsed(pc, [{ title: '本', source: 'kindle', highlights: [{ text: '点A', location: 10, chapter: '第1章', color: 'yellow' }] }], { now: '2025-02-01T11:00:00.000Z' });
  assert.equal(s.updated, 1);
  // 同期: サーバ側（PC を基準）でもスマホ側でも同じ結果
  for (const merged of [mergeLibraries(pc, phone), mergeLibraries(phone, pc)]) {
    const h = merged.highlights[id];
    assert.equal(h.favorite, true);
    assert.equal(h.userNote, 'スマホのメモ');
    assert.deepEqual(h.tags, ['大事']);
    assert.equal(h.chapter, '第1章', 'PC の取り込みで埋まった欄も残る');
    assert.equal(h.color, 'yellow');
  }
  assert.deepEqual(mergeLibraries(pc, phone), mergeLibraries(phone, pc), 'どちら向きに統合しても同じ');
});

test('同期: 自分の編集は新しい方が勝つ（★を外した・削除した も伝わる）', () => {
  const { pc, phone, id } = twoDevices();
  updateHighlight(pc, id, { favorite: true }, '2025-02-01T10:00:00.000Z');
  const synced = mergeLibraries(phone, pc);
  updateHighlight(synced, id, { favorite: false }, '2025-02-02T10:00:00.000Z');
  mergeParsed(pc, [{ title: '本', source: 'kindle', highlights: [{ text: '点A', location: 10, chapter: '第1章' }] }], { now: '2025-02-03T00:00:00.000Z' });
  const merged = mergeLibraries(pc, synced);
  assert.equal(merged.highlights[id].favorite, false, '後から外した★が、PC の取り込みより優先される');
  assert.equal(merged.highlights[id].chapter, '第1章');
  const idB = highlightIdFor(bookIdFor('本'), '点B');
  updateHighlight(phone, idB, { deleted: true }, '2025-02-04T00:00:00.000Z');
  assert.equal(mergeLibraries(pc, phone).highlights[idB].deleted, true);
});

test('同期: 本の削除は PC の取り込みに上書きされない・古い形式のデータ（userUpdatedAt 無し）の編集も守る', () => {
  const { pc, phone, id } = twoDevices();
  const bookId = bookIdFor('本');
  deleteBook(phone, bookId, '2025-02-01T10:00:00.000Z');
  mergeParsed(pc, [{ title: '本', source: 'kindle', highlights: [{ text: '点A', location: 10, color: 'blue' }] }], { now: '2025-02-01T11:00:00.000Z' });
  const merged = mergeLibraries(pc, phone);
  assert.equal(merged.books[bookId].deleted, true);
  assert.ok(!listBooks(merged).some((b) => b.id === bookId));
  // 古い版のアプリで付けた★（userUpdatedAt が無い）
  const legacyPhone = structuredClone(twoDevices().phone);
  Object.assign(legacyPhone.highlights[id], { favorite: true, updatedAt: '2025-03-01T00:00:00.000Z' });
  const legacyPc = twoDevices().pc;
  mergeParsed(legacyPc, [{ title: '本', source: 'kindle', highlights: [{ text: '点A', location: 10, chapter: '章' }] }], { now: '2025-03-02T00:00:00.000Z' });
  assert.equal(mergeLibraries(legacyPc, legacyPhone).highlights[id].favorite, true);
});

test('同期: 伸ばしたハイライトで置き換わった古い点は、どちらの端末から来ても消えたまま', () => {
  const { pc, phone } = twoDevices();
  mergeParsed(pc, [{ title: '本', source: 'kindle', highlights: [{ text: '点Aを伸ばした', location: 10, locationEnd: 12 }] }], { now: '2025-02-01T00:00:00.000Z' });
  const oldId = highlightIdFor(bookIdFor('本'), '点A');
  updateHighlight(phone, oldId, { userNote: '古い方に書いたメモ' }, '2025-01-15T00:00:00.000Z');
  const merged = mergeLibraries(phone, pc);
  assert.equal(merged.highlights[oldId].deleted, true);
  assert.deepEqual(bookHighlights(merged, bookIdFor('本')).map((h) => h.text), ['点Aを伸ばした', '点B']);
});

// ---- 20260927-playbooks-short-highlight-swallowed ----

test('Play ブックス: 別ページの長いハイライトに含まれる短いハイライトも両方残る（再取り込みでも）', () => {
  const lib = emptyLibrary();
  const book = { title: '深い集中', source: 'playbooks', highlights: [
    { text: '注意は最も希少な資源である。何に注意を向けるかが、その人の人生をつくる。', page: '12' },
    { text: '注意は最も希少な資源', page: '88' },
  ] };
  mergeParsed(lib, [book], { now: T1 });
  mergeParsed(lib, [book], { now: '2025-02-01T00:00:00.000Z' });
  assert.equal(bookHighlights(lib, bookIdFor('深い集中')).length, 2);
});

test('Kindle: 位置の無い（ページだけの）クリッピングは、別ページなら両方残し、同じページなら伸ばした方に置き換える', () => {
  const lib = emptyLibrary();
  mergeParsed(lib, [{ title: 'PDF', source: 'kindle', highlights: [{ text: '短い文', page: '3' }, { text: '短い文を含む長い文', page: '9' }] }], { now: T1 });
  assert.equal(bookHighlights(lib, bookIdFor('PDF')).length, 2);
  const lib2 = emptyLibrary();
  mergeParsed(lib2, [{ title: 'PDF', source: 'kindle', highlights: [{ text: '短い文', page: '3' }] }], { now: T1 });
  mergeParsed(lib2, [{ title: 'PDF', source: 'kindle', highlights: [{ text: '短い文を伸ばした', page: '3' }] }], { now: '2025-02-01T00:00:00.000Z' });
  assert.deepEqual(bookHighlights(lib2, bookIdFor('PDF')).map((h) => h.text), ['短い文を伸ばした']);
});

// ---- 20260927-truncated-filename-collision-reassigns-note ----

test('書名の先頭 80 文字が同じ本が後から増えても、既存のノート（と自分のメモ）は同じ本のまま', async () => {
  const { loadVaultOwners } = await import('../web/core/obsidian.js');
  const head = 'あ'.repeat(85);
  const lib = emptyLibrary();
  mergeParsed(lib, [{ title: head + '（上）', source: 'kindle', highlights: [{ text: '上巻の点', location: 1 }] }], { now: T1 });
  const disk = new Map();
  const read = async (p) => disk.get(p) ?? null;
  const exportAll = async () => {
    const files = renderVault(lib, null, { owners: await loadVaultOwners(read) });
    const plan = await planVaultWrite(files, read);
    for (const w of plan.writes) disk.set(w.path, w.content);
    for (const d of plan.deletes) disk.delete(d);
  };
  await exportAll();
  const [path] = [...disk.keys()].filter((p) => p.includes('/Books/'));
  disk.set(path, disk.get(path) + '上巻の感想\n');
  // ID の並びで前に来る別の巻を、何冊か足す
  for (const vol of ['（中）', '（下）', '（外伝）', '（別巻）']) {
    mergeParsed(lib, [{ title: head + vol, source: 'kindle', highlights: [{ text: vol + 'の点', location: 1 }] }], { now: '2025-02-01T00:00:00.000Z' });
    await exportAll();
    const note = disk.get(path);
    assert.match(note, /上巻の点/, `${vol} を足しても、元のノートは上巻のまま`);
    assert.match(note, /上巻の感想/);
    assert.ok(!note.includes(vol + 'の点'), '他の巻の点が混ざらない');
  }
  assert.equal([...disk.keys()].filter((p) => p.includes('/Books/')).length, 5);
});

// ---- 20260927-import-merge-diverges-backup-analysis ----

test('バックアップの取り込み: ライブラリと分析結果の扱いを 1 か所に統一（新しい分析だけ採用）', async () => {
  const { applyImport } = await import('../web/core/importing.js');
  const { parseFiles } = await import('../web/core/parsers/index.js');
  const lib = sampleLibrary();
  const older = { createdAt: '2025-01-01T00:00:00.000Z', lines: [], planes: [] };
  const newer = { createdAt: '2025-06-01T00:00:00.000Z', lines: [{ id: 'x' }], planes: [] };
  const enc = (o) => new TextEncoder().encode(JSON.stringify(o));
  // 旧形式（ライブラリに analysis を足したもの）と新形式（format 付き）の両方を読む
  const { books, backups } = await parseFiles([
    { name: 'old-backup.json', bytes: enc({ ...lib, analysis: newer }) },
    { name: 'new-backup.json', bytes: enc({ format: 'book-highlights/backup', version: 1, library: lib, analysis: older }) },
  ]);
  assert.equal(backups.length, 2);
  assert.ok(!('analysis' in backups[0].library), 'ライブラリに analysis が混ざらない');
  const r1 = applyImport({ library: emptyLibrary(), analysis: older }, { books, backups });
  assert.deepEqual(r1.analysis, newer, '手元より新しい分析は採用');
  assert.equal(r1.stats.highlights, 48);
  const r2 = applyImport({ library: emptyLibrary(), analysis: { ...newer, createdAt: '2026-01-01T00:00:00.000Z' } }, { books, backups });
  assert.equal(r2.analysis.createdAt, '2026-01-01T00:00:00.000Z', '手元の方が新しければそのまま');
  assert.equal(r2.analysisChanged, false);
});

// ---- 20260927-pc-job-result-lost-on-poll-failure ----

test('PC の分析の待機: 通信が一時的に失敗しても待ち続け、終われば結果を返す。長く途切れたら「不明」で止まる', async () => {
  const { followJob } = await import('../web/core/jobs.js');
  const seq = [{ running: true, stage: 'lines' }, new Error('offline'), new Error('offline'), { running: false, stage: 'done' }];
  const updates = [];
  const done = await followJob({ fetchJob: async () => { const x = seq.shift(); if (x instanceof Error) throw x; return x; }, onUpdate: (u) => updates.push(u), sleep: async () => {} });
  assert.equal(done.stage, 'done');
  assert.ok(updates.some((u) => u.reconnecting === 2), '再接続中であることを知らせる');
  const lost = await followJob({ fetchJob: async () => { throw new Error('offline'); }, onUpdate: () => {}, sleep: async () => {}, maxFailures: 3 });
  assert.equal(lost.lost, true);
});

// ---- 20260927-web-root-ignored-on-pc-export / 20260927-auto-vault-export-after-sync ----

import { mkdtempSync, existsSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';
import { createStore } from '../cli/store.js';
import { createCompanionServer } from '../cli/server.js';

const tmp = (p) => mkdtempSync(path.join(tmpdir(), p));

async function companionFor(config) {
  const store = createStore(tmp('bh-issue-data-'));
  const vault = tmp('bh-issue-vault-');
  await store.saveConfig({ vault, ...config });
  const server = createCompanionServer({ store, log: () => {}, autoExportDelay: 20, catalogFetch: async () => new Response('{}') });
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${server.address().port}`;
  const call = async (p, body, method = body ? 'POST' : 'GET') => (await fetch(base + p, { method, body: body && JSON.stringify(body) })).json();
  return { store, vault, server, call };
}

const until = async (cond, ms = 3000) => {
  const end = Date.now() + ms;
  while (!(await cond())) {
    if (Date.now() > end) throw new Error('timeout');
    await new Promise((r) => setTimeout(r, 20));
  }
};

test('「PC に書き出す」は Web で設定したフォルダ名を使い、PC の設定にも残す（画面の表示と出力先が一致）', async () => {
  const { store, vault, server, call } = await companionFor({ root: 'Highlights' });
  try {
    await call('/api/library/merge', sampleLibrary());
    const r = await call('/api/obsidian/export', { root: '読書ノート' });
    assert.equal(r.root, '読書ノート');
    assert.ok(existsSync(path.join(vault, '読書ノート/Books/小さな習慣の力.md')));
    assert.equal((await store.config()).root, '読書ノート', '次からの自動書き出し・bh obsidian も同じ場所');
    const info = await call('/api/info');
    assert.equal(info.root, '読書ノート');
    assert.equal(info.vaultPath, path.join(vault, '読書ノート'));
    // フォルダ名に「..」や「/」を入れても Vault の外には出ない
    const evil = await call('/api/obsidian/export', { root: '../../外' });
    assert.ok(!evil.root.includes('/') && !evil.root.startsWith('.'));
    assert.ok(existsSync(path.join(vault, evil.root)));
  } finally {
    server.close();
  }
});

test('同期・取り込みのあと Vault を自動で書き出し、最後に書き出した時刻を返す（オフにもできる）', async () => {
  const { vault, server, call } = await companionFor({});
  try {
    assert.equal((await call('/api/info')).lastExport, null);
    await call('/api/library/merge', sampleLibrary());
    await until(async () => (await call('/api/info')).lastExport);
    const { lastExport } = await call('/api/info');
    assert.equal(lastExport.trigger, 'sync');
    assert.ok(lastExport.written > 0 && !lastExport.error);
    assert.ok(Date.now() - new Date(lastExport.at).getTime() < 10000);
    assert.ok(existsSync(path.join(vault, 'Highlights/Index.md')));
  } finally {
    server.close();
  }
  const off = await companionFor({ autoExport: false });
  try {
    await off.call('/api/library/merge', sampleLibrary());
    await new Promise((r) => setTimeout(r, 150));
    assert.equal((await off.call('/api/info')).lastExport, null);
    assert.equal(readdirSync(off.vault).length, 0);
  } finally {
    off.server.close();
  }
});

test('bh import は Vault を設定していれば続けて書き出し、bh config で最後の書き出し時刻が見える', async () => {
  const run = promisify(execFile);
  const BH = path.join(path.dirname(fileURLToPath(import.meta.url)), '../cli/bh.js');
  const env = { ...process.env, BH_DATA: tmp('bh-issue-cli-') };
  const vault = tmp('bh-issue-cli-vault-');
  await run('node', [BH, 'config', 'vault', vault], { env });
  const fixture = path.join(path.dirname(fileURLToPath(import.meta.url)), 'fixtures/kindle-export-ja.html');
  const out = await run('node', [BH, 'import', fixture], { env });
  assert.match(out.stdout, /Obsidian: 書き込み \d+/);
  assert.ok(existsSync(path.join(vault, 'Highlights/Books/対人関係の地図.md')));
  const cfg = await run('node', [BH, 'config'], { env });
  assert.match(cfg.stdout, /最後に Vault に書き出した時刻: .*（import \/ 書き込み \d+ 件）/);
  const skip = await run('node', [BH, 'import', fixture, '--no-obsidian'], { env });
  assert.doesNotMatch(skip.stdout, /Obsidian:/);
});

// ---- 20260927-recommendation-feedback ----

import { setFeedback, feedbackFor } from '../web/core/model.js';
import { createLlmClient } from '../web/core/analysis/llm.js';
import { recommendBooks } from '../web/core/analysis/pipeline.js';
import { startFakeLlm } from './helpers/fake-llm.js';

test('おすすめへの反応（読んだ／読みたい／興味なし）: 付け外し・同期で新しい方が残る', () => {
  const pc = emptyLibrary();
  setFeedback(pc, { title: '哲学の入口', author: '著者 C' }, 'want', '2025-01-01T00:00:00.000Z');
  assert.equal(feedbackFor(pc, '哲学の入口')?.status, 'want');
  const phone = structuredClone(pc);
  setFeedback(phone, { title: '哲学の入口' }, 'read', '2025-01-02T00:00:00.000Z');
  setFeedback(pc, { title: '行動を変える技術' }, 'no', '2025-01-03T00:00:00.000Z');
  for (const m of [mergeLibraries(pc, phone), mergeLibraries(phone, pc)]) {
    assert.equal(feedbackFor(m, '哲学の入口').status, 'read');
    assert.equal(feedbackFor(m, '行動を変える技術').status, 'no');
  }
  // 同じ反応をもう一度押すと外れる
  setFeedback(phone, { title: '哲学の入口' }, 'read', '2025-01-04T00:00:00.000Z');
  assert.equal(feedbackFor(phone, '哲学の入口'), null);
});

test('おすすめの選定に反応を使う: 反応済みの本は候補から外し、好み（読みたい／興味なし）をプロンプトに伝える', async () => {
  const fake = await startFakeLlm();
  try {
    const lib = sampleLibrary();
    setFeedback(lib, { title: '行動を変える技術' }, 'no');
    setFeedback(lib, { title: '習慣の科学' }, 'want');
    const analysis = { planes: [{ id: 'p1', name: '面1', summary: 's' }, { id: 'p2', name: '面2', summary: 's' }], solid: { core: 'c', questions: [] } };
    const vol = (id, title) => ({ id, volumeInfo: { title, authors: ['著者'], infoLink: `https://books.google.com/?id=${id}` } });
    const fetchImpl = async (url) => {
      const q = decodeURIComponent(new URL(url).searchParams.get('q') || '');
      const items = q.includes('習慣') ? [vol('a', '習慣の科学'), vol('b', '行動を変える技術'), vol('c', '集中の技法')] : [vol('d', '哲学の入口')];
      return new Response(JSON.stringify({ items }));
    };
    const llm = createLlmClient({ baseUrl: fake.url, chatModel: 'fake-chat' });
    const recs = await recommendBooks({ library: lib, analysis, llm, fetchImpl });
    const titles = recs.map((r) => r.title);
    assert.ok(!titles.includes('行動を変える技術') && !titles.includes('習慣の科学'), '反応済みの本は選ばない');
    const pick = fake.calls.bodies.find((b) => b.response_format?.json_schema?.name === 'picks').messages[1].content;
    assert.doesNotMatch(pick, /『行動を変える技術』|『習慣の科学』/);
    const search = fake.calls.bodies.find((b) => b.response_format?.json_schema?.name === 'searches').messages[1].content;
    assert.match(search, /読みたいと言った本: 習慣の科学/);
    assert.match(search, /興味なしとした本: 行動を変える技術/);
  } finally {
    await fake.close();
  }
});

test('Obsidian のおすすめノートに反応と「読みたい本」が出る', () => {
  const lib = sampleLibrary();
  setFeedback(lib, { title: '次の本', author: '誰か' }, 'want');
  const analysis = {
    createdAt: T1, model: {}, stats: {},
    lines: [{ id: 'l1', name: '線', summary: 's', highlightIds: [Object.keys(lib.highlights)[0]], bookIds: [] }],
    planes: [{ id: 'p1', name: '面', summary: 's', lineIds: ['l1'] }],
    solid: { title: 't', core: 'c', relations: [], principles: [], questions: [] },
    isolated: [],
    recommendations: [{ title: '次の本', author: '誰か', reason: 'r', kind: 'deepen', planeId: 'p1' }],
  };
  const note = renderVault(lib, analysis).find((f) => f.path.endsWith('Recommendations.md')).content;
  assert.match(note, /- あなたの反応: 読みたい/);
  assert.match(note, /## 読みたい本\n\n- 次の本 — 誰か/);
});

// ---- レビュー指摘（2 回目）の回帰テスト ----

test('再分析で線・面の ID が変わっても、同じ名前のノート（と自分のメモ）を使い続ける', async () => {
  const { loadVaultOwners } = await import('../web/core/obsidian.js');
  const lib = sampleLibrary();
  const hs = Object.values(lib.highlights);
  const mk = (lineId, planeId) => ({
    createdAt: T1, model: {}, stats: {},
    lines: [{ id: lineId, name: '習慣の力', summary: 's', highlightIds: [hs[0].id, hs[1].id], bookIds: [] }],
    planes: [{ id: planeId, name: '自己管理', summary: 's', lineIds: [lineId] }],
    solid: { title: 't', core: 'c', relations: [], principles: [], questions: [] }, isolated: [], recommendations: [],
  });
  const disk = new Map();
  const read = async (p) => disk.get(p) ?? null;
  const exportWith = async (a) => {
    const plan = await planVaultWrite(renderVault(lib, a, { owners: await loadVaultOwners(read) }), read);
    for (const w of plan.writes) disk.set(w.path, w.content);
    for (const d of plan.deletes) disk.delete(d);
    return plan;
  };
  await exportWith(mk('l1', 'p1'));
  const line = 'Highlights/Lines/習慣の力.md';
  disk.set(line, disk.get(line) + '線についてのメモ\n');
  for (const [l, p] of [['l2', 'p2'], ['l3', 'p3']]) {
    const plan = await exportWith(mk(l, p));
    assert.ok(disk.has(line) && disk.get(line).includes('線についてのメモ'));
    assert.ok(!disk.has('Highlights/Lines/習慣の力 (2).md'), '(2) を作らない');
    assert.ok(disk.has('Highlights/Planes/自己管理.md') && !disk.has('Highlights/Planes/自己管理 (2).md'));
    assert.deepEqual(plan.orphaned, []);
  }
});

test('削除した本の自分のメモ付きノートは、同名の別の本に使わない', async () => {
  const { loadVaultOwners } = await import('../web/core/obsidian.js');
  const head = 'い'.repeat(85);
  const lib = emptyLibrary();
  mergeParsed(lib, [{ title: head + 'A', source: 'kindle', highlights: [{ text: 'Aの点', location: 1 }] }], { now: T1 });
  const disk = new Map();
  const read = async (p) => disk.get(p) ?? null;
  const exportAll = async () => {
    const plan = await planVaultWrite(renderVault(lib, null, { owners: await loadVaultOwners(read) }), read);
    for (const w of plan.writes) disk.set(w.path, w.content);
    for (const d of plan.deletes) disk.delete(d);
  };
  await exportAll();
  const [aPath] = [...disk.keys()].filter((p) => p.includes('/Books/'));
  disk.set(aPath, disk.get(aPath) + 'Aのメモ\n');
  deleteBook(lib, bookIdFor(head + 'A'), '2025-02-01T00:00:00.000Z');
  mergeParsed(lib, [{ title: head + 'B', source: 'kindle', highlights: [{ text: 'Bの点', location: 1 }] }], { now: '2025-02-02T00:00:00.000Z' });
  await exportAll();
  assert.doesNotMatch(disk.get(aPath), /Bの点/, 'メモの残った A のノートに B を書かない');
  assert.match(disk.get(aPath), /Aのメモ/);
});

test('同期: 未同期の編集をした短いハイライトが、もう一方の端末で伸ばした版に置き換わっても編集を引き継ぐ', () => {
  const { pc, phone, id } = twoDevices();
  updateHighlight(phone, id, { favorite: true, userNote: '短い方のメモ' }, '2025-02-01T10:00:00.000Z');
  mergeParsed(pc, [{ title: '本', source: 'kindle', highlights: [{ text: '点Aを伸ばした', location: 10, locationEnd: 12 }] }], { now: '2025-02-01T11:00:00.000Z' });
  for (const m of [mergeLibraries(pc, phone), mergeLibraries(phone, pc)]) {
    const live = bookHighlights(m, bookIdFor('本')).find((h) => h.text === '点Aを伸ばした');
    assert.equal(live.userNote, '短い方のメモ');
    assert.equal(live.favorite, true);
  }
});

test('同期: 古い版のデータで付けた編集は、もう一方の取り込みで updatedAt が進んでも負けない', () => {
  const pc = emptyLibrary();
  mergeParsed(pc, [{ title: '本', source: 'kindle', highlights: [{ text: '点A', location: 10 }] }], { now: '2025-01-01T00:00:00.000Z' });
  const id = highlightIdFor(bookIdFor('本'), '点A');
  // 古い版: userUpdatedAt が無く、編集で updatedAt だけ進んだ
  Object.assign(pc.highlights[id], { userNote: 'old', updatedAt: '2025-01-02T00:00:00.000Z' });
  const phone = structuredClone(pc);
  Object.assign(phone.highlights[id], { userNote: 'new', updatedAt: '2025-01-03T00:00:00.000Z' });
  mergeParsed(pc, [{ title: '本', source: 'kindle', highlights: [{ text: '点A', location: 10, chapter: '章' }] }], { now: '2025-01-04T00:00:00.000Z' });
  assert.equal(pc.highlights[id].userUpdatedAt, '2025-01-02T00:00:00.000Z', '取り込みの前の編集時刻を残す');
  assert.equal(mergeLibraries(pc, phone).highlights[id].userNote, 'new');
  assert.equal(mergeLibraries(phone, pc).highlights[id].userNote, 'new');
});

test('同期: ソースの並びと置き換え先は、どちら向きに統合しても同じ', () => {
  const a = emptyLibrary();
  mergeParsed(a, [{ title: '本', source: 'playbooks', highlights: [{ text: 'x', page: '1' }] }], { now: T1 });
  const b = emptyLibrary();
  mergeParsed(b, [{ title: '本', source: 'kindle', highlights: [{ text: 'y', location: 1 }] }], { now: T1 });
  assert.deepEqual(mergeLibraries(a, b).books[bookIdFor('本')].sources, mergeLibraries(b, a).books[bookIdFor('本')].sources);
});

test('frontmatter: 空白の無いコロンを含むキー・カンマ区切りの tags・ノートの種類ごとの生成キー', async () => {
  const lib = sampleLibrary();
  const generated = bookNote(lib);
  const a = mergeManaged(generated.replace('tags: ["book-highlights/book"]\n', 'tags: ["book-highlights/book"]\nref:1: x\n'), generated);
  assert.match(a, /^ref:1: x$/m);
  const once = mergeManaged(generated.replace('tags: ["book-highlights/book"]', 'tags: book-highlights/book, mytag'), generated);
  assert.match(once, /^tags: \["book-highlights\/book", "mytag"\]$/m);
  assert.equal(mergeManaged(once, generated), once, '2 回目も崩れない');
  // 本のノートに自分で keywords を足していたら、本を消しても削除しない
  const disk = new Map();
  const read = async (p) => disk.get(p) ?? null;
  for (const w of (await planVaultWrite(renderVault(lib, null), read)).writes) disk.set(w.path, w.content);
  const p = 'Highlights/Books/小さな習慣の力.md';
  disk.set(p, disk.get(p).replace('tags:', 'keywords: [習慣]\ntags:'));
  deleteBook(lib, bookIdFor('小さな習慣の力'));
  const plan = await planVaultWrite(renderVault(lib, null), read);
  assert.ok(plan.orphaned.includes(p) && !plan.deletes.includes(p));
});

test('planVaultWrite は書き出したファイルの持ち主を返す（「Obsidian で開く」で使う）', async () => {
  const lib = sampleLibrary();
  const plan = await planVaultWrite(renderVault(lib, null), async () => null);
  assert.equal(plan.owners['Highlights/Books/小さな習慣の力.md'], bookIdFor('小さな習慣の力'));
});

test('分析中に届いた同期の自動書き出しは、分析が失敗したあとで行う・/api/info は持ち主を返す', async () => {
  const { store, server, call } = await companionFor({});
  try {
    server.job.running = true;
    await call('/api/library/merge', sampleLibrary());
    await new Promise((r) => setTimeout(r, 120));
    assert.equal((await call('/api/info')).lastExport, null, '分析中は書き出さない');
    server.job.running = false;
    // チャットモデル未設定の分析はすぐ失敗する → 保留していた書き出しを行う
    await call('/api/analyze', { mode: 'analyze' });
    await until(async () => (await call('/api/info')).lastExport);
    const info = await call('/api/info');
    assert.equal(info.lastExport.trigger, 'sync');
    assert.equal(info.owners['Highlights/Books/小さな習慣の力.md'], bookIdFor('小さな習慣の力'));
    assert.ok(!(await store.state()).lastExport.error);
  } finally {
    server.close();
  }
});

test('Web から既定のフォルダ名を送らなければ、PC の設定は変わらない', async () => {
  const { store, vault, server, call } = await companionFor({ root: '読書' });
  try {
    await call('/api/library/merge', sampleLibrary());
    const r = await call('/api/obsidian/export', {});
    assert.equal(r.root, '読書');
    assert.equal((await store.config()).root, '読書');
    assert.ok(existsSync(path.join(vault, '読書/Index.md')));
  } finally {
    server.close();
  }
});

test('bh import: Vault が見つからなくても取り込みは成功（書き出しの失敗は警告）', async () => {
  const run = promisify(execFile);
  const BH = path.join(path.dirname(fileURLToPath(import.meta.url)), '../cli/bh.js');
  const env = { ...process.env, BH_DATA: tmp('bh-issue-cli2-') };
  await run('node', [BH, 'config', 'vault', path.join(tmpdir(), 'no-such-vault-' + Date.now())], { env });
  const fixture = path.join(path.dirname(fileURLToPath(import.meta.url)), 'fixtures/kindle-export-ja.html');
  const out = await run('node', [BH, 'import', fixture], { env });
  assert.match(out.stdout, /取り込み: 新しい点 2 件/);
  assert.match(out.stdout, /取り込みは保存しましたが、Obsidian への書き出しに失敗しました/);
  const list = await run('node', [BH, 'list'], { env });
  assert.match(list.stdout, /本 1 冊/);
});
