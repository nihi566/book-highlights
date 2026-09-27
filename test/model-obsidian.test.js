import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { bookHighlights, dailyPicks, deleteBook, emptyLibrary, highlightIdFor, bookIdFor, libraryStats, listBooks, mergeLibraries, mergeParsed, searchHighlights, updateHighlight } from '../web/core/model.js';
import { END, layoutKnowledgeMap, mergeManaged, planVaultWrite, renderVault } from '../web/core/obsidian.js';
import { createZip, readZip } from '../web/core/zip.js';
import { SAMPLE_BOOKS } from '../web/core/sample.js';

const T1 = '2025-01-01T00:00:00.000Z';
const T2 = '2025-02-01T00:00:00.000Z';

function sampleLibrary() {
  const lib = emptyLibrary();
  mergeParsed(lib, SAMPLE_BOOKS, { now: T1 });
  return lib;
}

test('mergeParsed: 取り込み・再取り込みで重複しない・空欄を補う', () => {
  const lib = emptyLibrary();
  const parsed = [{ title: '本A', author: '著者', source: 'kindle', highlights: [{ text: '一つ目', location: 10 }, { text: '二つ目' }] }];
  const s1 = mergeParsed(lib, parsed, { now: T1 });
  assert.deepEqual([s1.added, s1.booksAdded], [2, 1]);
  const s2 = mergeParsed(lib, [{ ...parsed[0], highlights: [{ text: '一つ目', location: 10, note: '後から付いたメモ' }] }], { now: T2 });
  assert.deepEqual([s2.added, s2.updated], [0, 1]);
  const id = highlightIdFor(bookIdFor('本A'), '一つ目');
  assert.equal(lib.highlights[id].note, '後から付いたメモ');
  assert.equal(libraryStats(lib).highlights, 2);
});

test('mergeParsed: Kindle で伸ばしたハイライトは古い方を置き換え、ユーザーの編集を引き継ぐ', () => {
  const lib = emptyLibrary();
  mergeParsed(lib, [{ title: '本', source: 'kindle', highlights: [{ text: '短い文', location: 100, locationEnd: 101 }] }], { now: T1 });
  const shortId = Object.keys(lib.highlights)[0];
  updateHighlight(lib, shortId, { favorite: true, tags: ['#大事', ' 大事 ', 'x'], userNote: '覚える' }, T1);
  assert.deepEqual(lib.highlights[shortId].tags, ['大事', 'x']);
  mergeParsed(lib, [{ title: '本', source: 'kindle', highlights: [{ text: '短い文を伸ばした', location: 100, locationEnd: 104 }] }], { now: T2 });
  const live = bookHighlights(lib, bookIdFor('本'));
  assert.equal(live.length, 1);
  assert.equal(live[0].text, '短い文を伸ばした');
  assert.equal(live[0].favorite, true);
  assert.equal(live[0].userNote, '覚える');
  // 古い短い版を再取り込みしても復活しない
  mergeParsed(lib, [{ title: '本', source: 'kindle', highlights: [{ text: '短い文', location: 100, locationEnd: 101 }] }], { now: T2 });
  assert.equal(bookHighlights(lib, bookIdFor('本')).length, 1);
});

test('削除（墓標）は再取り込みで復活しない・同期では新しい方が勝つ', () => {
  const lib = sampleLibrary();
  const [first] = listBooks(lib);
  const hl = bookHighlights(lib, first.id)[0];
  updateHighlight(lib, hl.id, { deleted: true }, T2);
  mergeParsed(lib, SAMPLE_BOOKS, { now: T2 });
  assert.equal(lib.highlights[hl.id].deleted, true);

  const phone = structuredClone(lib);
  updateHighlight(phone, hl.id, { deleted: false, favorite: true }, '2025-03-01T00:00:00.000Z');
  const merged = mergeLibraries(lib, phone);
  assert.equal(merged.highlights[hl.id].deleted, false);
  assert.equal(merged.highlights[hl.id].favorite, true);
  const reverse = mergeLibraries(phone, lib);
  assert.equal(reverse.highlights[hl.id].favorite, true, 'どちら向きに統合しても新しい編集が残る');

  deleteBook(lib, first.id, T2);
  assert.ok(!listBooks(lib).some((b) => b.id === first.id));
});

test('検索・一覧・今日の点', () => {
  const lib = sampleLibrary();
  assert.equal(listBooks(lib).length, 8);
  assert.equal(libraryStats(lib).highlights, 48);
  assert.deepEqual(libraryStats(lib).bySource, { kindle: 24, playbooks: 24 });
  // 語は本文・メモ・書名・著者のどこかにあればよい（AND 検索）
  const hits = searchHighlights(lib, '集中 深い');
  assert.equal(hits.length, 6);
  assert.ok(hits.every((h) => lib.books[h.bookId].title === '深い集中'));
  assert.equal(searchHighlights(lib, '集中 退屈').length, 1);
  assert.ok(searchHighlights(lib, '山田').length === 6, '著者名でも探せる');
  assert.equal(searchHighlights(lib, '', { source: 'playbooks' }).length, 24);
  const id = searchHighlights(lib, '注意は最も希少')[0].id;
  updateHighlight(lib, id, { tags: ['注意'] });
  assert.equal(searchHighlights(lib, '#注意')[0].id, id);
  const d = new Date(2025, 5, 1);
  assert.deepEqual(dailyPicks(lib, 3, d).map((h) => h.id), dailyPicks(lib, 3, d).map((h) => h.id));
});

function fakeAnalysis(lib) {
  const hs = Object.values(lib.highlights);
  return {
    createdAt: T2,
    model: { chat: 'fake', embed: 'tfidf' },
    stats: { points: hs.length, lines: 2, planes: 1 },
    lines: [
      { id: 'l1', name: '仕組み: 環境', summary: '仕組みが行動をつくる。', insight: '問い', keywords: ['仕組み'], highlightIds: [hs[0].id, hs[7].id], bookIds: [] },
      { id: 'l2', name: '注意の管理', summary: '注意は資源。', insight: '', keywords: [], highlightIds: [hs[6].id, hs[8].id], bookIds: [] },
    ],
    planes: [{ id: 'p1', name: '自己の設計', summary: '行動と注意を設計する。', lineIds: ['l1', 'l2'] }],
    solid: { title: '核', core: '小さな仕組み。', relations: [], principles: ['原則'], questions: ['問い'] },
    isolated: [hs[1].id],
    recommendations: [{ title: '次の本', author: '誰か', reason: '理由', kind: 'deepen', planeId: 'p1', verified: false }],
  };
}

test('renderVault: 本ノートにブロック ID、線ノートから点を埋め込み、Canvas は妥当な JSON', () => {
  const lib = sampleLibrary();
  const analysis = fakeAnalysis(lib);
  const files = renderVault(lib, analysis, { root: 'Highlights' });
  const paths = files.map((f) => f.path);
  assert.ok(paths.includes('Highlights/Index.md'));
  assert.ok(paths.includes('Highlights/Books/小さな習慣の力.md'));
  assert.ok(paths.includes('Highlights/Lines/仕組み 環境.md'), 'ファイル名に使えない文字は除く');
  assert.ok(paths.includes('Highlights/Planes/自己の設計.md'));
  assert.ok(paths.includes('Highlights/Knowledge Map.canvas'));
  assert.ok(paths.includes('Highlights/Recommendations.md'));

  const bookNote = files.find((f) => f.path === 'Highlights/Books/小さな習慣の力.md').content;
  assert.match(bookNote, /^---\ntitle: "小さな習慣の力"\nauthor: "山田 太郎"/);
  assert.match(bookNote, /> \[!quote\] 位置 \d+-\d+ · \d{4}-\d{2}-\d{2}\n> 行動を変えたいなら/);
  assert.match(bookNote, /\n\^h[0-9a-z]+\n/);
  assert.match(bookNote, /## 第2章 環境と自己像/);
  assert.match(bookNote, /\*\*メモ:\*\* 複利の考え方/);

  const line = files.find((f) => f.path.startsWith('Highlights/Lines/仕組み')).content;
  const hs = Object.values(lib.highlights);
  const book = lib.books[hs[0].bookId];
  assert.ok(line.includes(`![[Highlights/Books/${book.title}#^${hs[0].id}]]`));
  assert.ok(line.includes('[[Highlights/Planes/自己の設計|自己の設計]]'));

  const canvas = JSON.parse(files.find((f) => f.path.endsWith('.canvas')).content);
  assert.equal(canvas.nodes.length, 1 + 1 + 2);
  assert.ok(canvas.nodes.every((n) => Number.isFinite(n.x) && Number.isFinite(n.y)));
  assert.ok(canvas.nodes.some((n) => n.file === 'Highlights/Lines/注意の管理.md'));
  const layout = layoutKnowledgeMap(analysis);
  assert.equal(layout.edges.length, 3);
});

test('mergeManaged: 自分のメモと追加した frontmatter は残る。マーカーの無い同名ノートは上書きしない', () => {
  const lib = sampleLibrary();
  const f = renderVault(lib, null).find((x) => x.path === 'Highlights/Books/小さな習慣の力.md');
  const edited = f.content.replace('---\n#', 'rating: 5\n---\n#').replace(/## 自分のメモ\n\n$/, '## 自分のメモ\n\nとても良い本だった\n');
  const regenerated = f.content.replace('行動を変えたいなら', '行動を変えたいなら（更新）');
  const merged = mergeManaged(edited, regenerated);
  assert.ok(merged.includes('（更新）'));
  assert.ok(merged.includes('とても良い本だった'));
  assert.ok(merged.includes('rating: 5'));
  assert.equal(merged.match(/^title:/gm).length, 1);
  assert.equal(mergeManaged('# 自分で書いたノート', regenerated), null);
  assert.equal(mergeManaged(null, regenerated), regenerated);
});

test('planVaultWrite: 変更なしはスキップ、分析から外れたノートは自分のメモが無ければ削除', async () => {
  const lib = sampleLibrary();
  const analysis = fakeAnalysis(lib);
  const disk = new Map();
  const read = async (p) => disk.get(p) ?? null;
  const apply = (plan) => {
    for (const w of plan.writes) disk.set(w.path, w.content);
    for (const d of plan.deletes) disk.delete(d);
  };
  const first = await planVaultWrite(renderVault(lib, analysis), read);
  apply(first);
  assert.equal(first.deletes.length, 0);
  const again = await planVaultWrite(renderVault(lib, analysis), read);
  assert.equal(again.writes.length, 1, 'マニフェスト以外は変更なし');

  // 線 l1 にだけ自分のメモを書き、次の分析で l1 と l2 が消えた場合
  const l1Path = 'Highlights/Lines/仕組み 環境.md';
  disk.set(l1Path, disk.get(l1Path) + 'この線について考えたこと\n');
  const next = { ...analysis, lines: [{ ...analysis.lines[1], id: 'l3', name: '新しい線' }], planes: [{ ...analysis.planes[0], lineIds: ['l3'] }] };
  const plan = await planVaultWrite(renderVault(lib, next), read);
  assert.deepEqual(plan.deletes, ['Highlights/Lines/注意の管理.md']);
  assert.deepEqual(plan.orphaned, [l1Path]);
  assert.ok(plan.writes.some((w) => w.path === 'Highlights/Lines/新しい線.md'));
  // ユーザーが作った同名ノートは上書きしない
  disk.set('Highlights/Books/深い集中.md', '# 自分のノート');
  const plan2 = await planVaultWrite(renderVault(lib, next), read);
  assert.ok(plan2.skipped.includes('Highlights/Books/深い集中.md'));
});

test('zip: 書き出しと読み込みの往復、system unzip でも検証', async () => {
  const files = [
    { name: 'Highlights/Books/日本語の本.md', content: '# こんにちは\n' },
    { name: 'Highlights/Index.md', content: 'x'.repeat(1000) },
  ];
  const zip = createZip(files);
  const back = await readZip(zip);
  assert.deepEqual(back.map((e) => [e.name, new TextDecoder().decode(e.bytes)]), files.map((f) => [f.name, f.content]));
  let hasUnzip = true;
  try {
    execFileSync('unzip', ['-v'], { stdio: 'ignore' });
  } catch {
    hasUnzip = false;
  }
  if (hasUnzip) {
    const dir = mkdtempSync(path.join(tmpdir(), 'bh-zip-'));
    const p = path.join(dir, 'vault.zip');
    writeFileSync(p, zip);
    const out = execFileSync('unzip', ['-t', p]).toString();
    assert.match(out, /No errors detected/);
  }
});
