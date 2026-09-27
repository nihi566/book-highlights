import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { parseFiles } from '../web/core/parsers/index.js';
import { parseClippingMeta, parseKindleClippings, splitTitleAuthor } from '../web/core/parsers/kindle-clippings.js';
import { parseKindleExportHtml, parseNoteHeading } from '../web/core/parsers/kindle-notebook.js';
import { parsePlayBooksHtml, parsePlayBooksMarkdown, titleFromFileName } from '../web/core/parsers/playbooks.js';
import { colorName } from '../web/core/parsers/blocks.js';
import { createZip } from '../web/core/zip.js';
import { makeDeflateZip, playBooksDocumentXml } from './helpers/docx.js';

const fixture = (name) => readFile(new URL(`./fixtures/${name}`, import.meta.url));
const fixtureText = async (name) => (await fixture(name)).toString('utf8');

test('My Clippings.txt: 日本語・英語、BOM と CRLF、伸ばしたハイライト、メモの紐付け、ブックマーク除外', async () => {
  const books = parseKindleClippings(await fixtureText('My Clippings.txt'));
  const habit = books.find((b) => b.title === '小さな習慣の力');
  assert.equal(habit.author, '山田 太郎');
  // 伸ばす前の短いハイライトは dedupe 前の段階では残る（取り込み時に mergeParsed が除く）
  const long = habit.highlights.find((h) => h.text.includes('最初は見えない'));
  assert.equal(long.location, 120);
  assert.equal(long.locationEnd, 123);
  assert.equal(long.note, '複利の考え方は勉強にも当てはまる');
  assert.equal(long.createdAt, new Date(2024, 0, 5, 21, 3, 30).toISOString());
  const env = habit.highlights.find((h) => h.text.startsWith('環境は'));
  assert.equal(env.page, '45');
  assert.ok(!habit.highlights.some((h) => h.text === ''), 'ブックマークは含めない');

  const nested = books.find((b) => b.title === '心理学 (入門) の本');
  assert.equal(nested.author, '佐藤 (編)');
  assert.equal(nested.highlights[0].page, 'xii');
  assert.equal(nested.highlights[0].createdAt, new Date(2023, 11, 1, 15, 0, 0).toISOString());

  const deep = books.find((b) => b.title === 'Deep Work');
  assert.equal(deep.highlights[0].note, 'Use this for the weekly review.');
  assert.equal(deep.highlights[0].page, '12');
  const abbreviated = deep.highlights.find((h) => h.text.startsWith('An abbreviated'));
  assert.deepEqual([abbreviated.location, abbreviated.locationEnd], [1234, 1256]);
});

test('クリッピングのメタ行: 旧形式・英語の at location・ローマ数字ページ', () => {
  assert.deepEqual(parseClippingMeta('- ハイライト ページ222 | 位置No. 3396-3396 | 追加日： 2013年6月17日 (月曜日) 20:31:09'), {
    kind: 'highlight',
    location: 3396,
    locationEnd: 3396,
    page: '222',
    createdAt: new Date(2013, 5, 17, 20, 31, 9).toISOString(),
  });
  const en = parseClippingMeta('- Your Highlight at location 9723-9727 | Added on Sunday, 2 January 2022 13:17:22');
  assert.equal(en.kind, 'highlight');
  assert.equal(en.location, 9723);
  assert.equal(en.locationEnd, 9727);
  assert.equal(parseClippingMeta('- Your Bookmark on page ix | location 247 | Added on Sunday, 18 February 2018 22:30:47').kind, 'bookmark');
  assert.equal(parseClippingMeta('- xiiiページ|位置No. 186-188のハイライト |作成日: 2024年2月1日木曜日 14:09:33').page, 'xiii');
  assert.deepEqual(splitTitleAuthor('﻿タイトルのみ'), { title: 'タイトルのみ', author: '' });
});

test('Kindle のエクスポート HTML（日本語・新形式）: 色・ページ・位置（カンマ区切り）・章・メモ', async () => {
  const [book] = parseKindleExportHtml(await fixtureText('kindle-export-ja.html'));
  assert.equal(book.title, '対人関係の地図');
  assert.equal(book.author, '伊藤 美咲　著');
  assert.equal(book.highlights.length, 2, 'メモはハイライトに付き、ブックマークは除かれる');
  const [a, b] = book.highlights;
  assert.equal(a.color, 'pink');
  assert.equal(a.page, '23');
  assert.equal(a.location, 607);
  assert.equal(a.chapter, '第1章　課題を分ける');
  assert.equal(a.note, '上司との関係に使えそう');
  assert.equal(b.location, 1210);
  assert.equal(b.color, 'yellow');
});

test('Kindle のエクスポート HTML（旧形式・壊れたタグ）', async () => {
  const [book] = parseKindleExportHtml(await fixtureText('kindle-export-old.html'));
  assert.equal(book.title, 'The Art of Focus');
  assert.equal(book.author, 'Doe, Jane');
  assert.equal(book.highlights.length, 2);
  assert.equal(book.highlights[0].text, 'Attention is the rarest resource, and the most valuable.');
  assert.equal(book.highlights[0].note, 'Key idea of the book');
  assert.equal(book.highlights[1].page, '30');
  assert.equal(book.highlights[1].location, 1369);
  assert.equal(book.highlights[1].color, 'blue');
  assert.equal(parseNoteHeading('Markierung(<span class="highlight_yellow">gelb</span>) - Position 1968').location, 1968);
});

test('Play ブックスのメモ（HTML）: 色別セクションの重複を除き、章・色・メモ・日付を取る', async () => {
  const [book] = parsePlayBooksHtml(await fixtureText('playbooks-ja.html'), 'fallback');
  assert.equal(book.title, '深い集中');
  assert.equal(book.author, '佐藤 花子');
  assert.equal(book.source, 'playbooks');
  assert.equal(book.highlights.length, 3, '同じ注釈は 1 回だけ。ブックマークと表示不可テキストは除く');
  const [a, b, c] = book.highlights;
  assert.equal(a.chapter, '第1章 注意という資源');
  assert.equal(a.color, 'yellow');
  assert.equal(a.page, '12');
  assert.equal(a.createdAt, new Date(2024, 1, 3).toISOString());
  assert.equal(b.color, 'green');
  assert.equal(b.note, '朝の30分で試す');
  assert.equal(b.chapter, '第2章 集中の訓練');
  assert.equal(c.color, 'blue');
});

test('Play ブックスのメモ（docx、deflate 圧縮）: 背景色で本文とメモを分ける', async () => {
  const xml = playBooksDocumentXml({
    title: '心の平静について',
    author: '高橋 次郎',
    annotations: [
      { chapter: '第1章', text: '自分の力の及ぶことと及ばないことを区別せよ。', fill: 'fde096', note: '毎朝読む', date: '2024年4月1日', page: '10' },
      { chapter: '第2章', text: '困難は、徳を鍛えるための訓練の場である。', fill: 'ffb8a1', date: 'April 2, 2024', page: 'xii' },
    ],
  });
  const docx = makeDeflateZip([
    { name: '[Content_Types].xml', content: '<Types/>' },
    { name: 'word/document.xml', content: xml },
  ]);
  const { books, results } = await parseFiles([{ name: '「心の平静について」のメモ.docx', bytes: docx }]);
  assert.equal(results[0].format, 'playbooks');
  assert.equal(books[0].title, '心の平静について');
  assert.equal(books[0].author, '高橋 次郎');
  assert.deepEqual(
    books[0].highlights.map((h) => [h.text, h.note, h.color, h.page, h.chapter]),
    [
      ['自分の力の及ぶことと及ばないことを区別せよ。', '毎朝読む', 'yellow', '10', '第1章'],
      ['困難は、徳を鍛えるための訓練の場である。', '', 'red', 'xii', '第2章'],
    ],
  );
});

test('Play ブックスのメモ（Markdown 書き出し）', async () => {
  const [book] = parsePlayBooksMarkdown(await fixtureText('playbooks-ja.md'));
  assert.equal(book.title, '書くことで考える');
  assert.equal(book.highlights.length, 2, 'ブックマーク行は除く');
  assert.equal(book.highlights[0].text, '書くことは、考えを記録することではなく、考えること自体である!');
  assert.equal(book.highlights[0].page, '15');
  assert.equal(book.highlights[0].chapter, '第1章');
  assert.equal(book.highlights[1].text, 'メモは一つのアイデアにつき一枚にする');
  assert.equal(book.highlights[1].note, 'ツェッテルカステンの基本');
});

test('parseFiles: 形式の自動判定と zip の展開、エラー報告', async () => {
  const names = ['My Clippings.txt', 'kindle-export-ja.html', 'kindle-notebook.json', 'playbooks-ja.html', 'playbooks-ja.md'];
  const files = await Promise.all(names.map(async (name) => ({ name, content: new Uint8Array(await fixture(name)) })));
  const zip = createZip([...files, { name: '__MACOSX/._junk', content: 'x' }, { name: 'readme.txt', content: 'hello' }]);
  const { books, results } = await parseFiles([{ name: 'highlights.zip', bytes: zip }]);
  const byName = Object.fromEntries(results.map((r) => [r.name.replace('highlights.zip/', ''), r]));
  assert.equal(byName['My Clippings.txt'].format, 'kindle-clippings');
  assert.equal(byName['kindle-export-ja.html'].format, 'kindle-export');
  assert.equal(byName['kindle-notebook.json'].format, 'kindle-notebook');
  assert.equal(byName['playbooks-ja.html'].format, 'playbooks');
  assert.equal(byName['playbooks-ja.md'].format, 'playbooks');
  assert.match(byName['readme.txt'].error, /My Clippings/);
  assert.ok(!results.some((r) => r.name.includes('__MACOSX')));
  const nb = books.find((b) => b.title === '時間の使い方の哲学');
  assert.equal(nb.asin, 'B000TEST01');
  assert.equal(nb.highlights[1].location, 1020);
  assert.equal(nb.highlights[2].kind, 'note', 'ハイライト無しのメモは単独の点になる');
  assert.ok(!books.some((b) => b.title === '空の本'));
});

test('Shift_JIS のテキストも読める', async () => {
  // 「本 (著者)」のクリッピングを Shift_JIS で用意
  const sjis = new Uint8Array([0x96, 0x7b, 0x20, 0x28, 0x92, 0x98, 0x8e, 0xd2, 0x29, 0x0d, 0x0a]);
  const rest = new TextEncoder().encode('- Your Highlight on page 1 | Location 1-2 | Added on Sunday, January 1, 2023 1:00:00 PM\r\n\r\nHello\r\n==========\r\n');
  const bytes = new Uint8Array([...sjis, ...rest]);
  const { books } = await parseFiles([{ name: 'My Clippings.txt', bytes }]);
  assert.equal(books[0].title, '本');
  assert.equal(books[0].author, '著者');
});

test('色名とファイル名からの書名推測', () => {
  assert.equal(colorName('fde096'), 'yellow');
  assert.equal(colorName('c5e1a5'), 'green');
  assert.equal(colorName('93e3ed'), 'blue');
  assert.equal(colorName('ffb8a1'), 'red');
  assert.equal(colorName('ffffff'), '');
  assert.equal(titleFromFileName('Notes from _Drive_.docx'), 'Drive');
  assert.equal(titleFromFileName('「深い集中」のメモ.html'), '深い集中');
});
