import { test } from 'node:test';
import assert from 'node:assert/strict';
import { emptyLibrary, mergeParsed } from '../web/core/model.js';
import { SAMPLE_BOOKS } from '../web/core/sample.js';
import { createLlmClient } from '../web/core/analysis/llm.js';
import { recommendBooks } from '../web/core/analysis/pipeline.js';
import { loadMarks, memoryStore, parseWishlist, toRecommendWishlist, wishlistForRecommend } from '../web/core/wishlist.js';
import { startFakeLlm } from './helpers/fake-llm.js';

const analysis = { planes: [{ id: 'p1', name: '習慣', summary: '習慣と行動を変える仕組み' }, { id: 'p2', name: '哲学', summary: '問いを立てる' }], solid: { core: '小さな習慣が大きな変化を生む', questions: [] } };
const vol = (id, title) => ({ id, volumeInfo: { title, authors: ['著者'], infoLink: `https://books.google.com/?id=${id}` } });

test('おすすめに渡す欲しい本: 購入済み（タグも）・読んだは除外の印を付け、送られてきた値は形を確かめて捨てる', () => {
  const w = parseWishlist({
    format: 'kindle-wishlist',
    version: 1,
    books: [
      { asin: 'B0WISH0001', title: '習慣を続ける本', price: 990, wanted: true },
      { asin: 'B0WISH0002', title: '読み放題の習慣本', ku: true, wanted: true },
      { asin: 'B0WISH0003', title: '買った本', price: 500, purchased: true },
      { asin: 'B0WISH0004', title: 'タグで買った本', price: 700, wanted: true },
      { asin: 'B0WISH0005', title: '読んだ本', price: 800, wanted: true },
    ],
  });
  const store = memoryStore({ 'book-tag:B0WISH0004': 'purchased', 'book-tag:B0WISH0005': 'seen' });
  const list = toRecommendWishlist(w.books.map((book) => ({ book, marks: loadMarks(book, store) })));
  assert.deepEqual(list.map((b) => [b.title, b.skip]), [['習慣を続ける本', false], ['読み放題の習慣本', false], ['買った本', true], ['タグで買った本', true], ['読んだ本', true]]);
  assert.deepEqual(list[1], { title: '読み放題の習慣本', asin: 'B0WISH0002', price: null, ku: true, skip: false });
  // PC に送られてきた値（信用しない）
  const raw = [{ title: '  ok  ', asin: 'bad asin', price: -5, ku: 'yes', skip: 1 }, { title: 3 }, null, { title: 'x'.repeat(500), price: 1200 }];
  const v = wishlistForRecommend(raw);
  assert.deepEqual(v[0], { title: 'ok', asin: '', price: null, ku: false, skip: false });
  assert.equal(v.length, 2);
  assert.equal(v[1].title.length, 200);
  assert.equal(v[1].price, 1200);
  assert.deepEqual(wishlistForRecommend('nope'), []);
  assert.equal(wishlistForRecommend(Array.from({ length: 2000 }, (_, i) => ({ title: `本${i}` }))).length, 1000);
});

test('おすすめ: 欲しい本を候補に混ぜて理由付きで選び（価格・KU 付き）、購入済み・読んだ本は出さない', async () => {
  const fake = await startFakeLlm();
  try {
    const lib = emptyLibrary();
    mergeParsed(lib, SAMPLE_BOOKS);
    const wishlist = [
      { title: '習慣を続ける本', asin: 'B0WISH0001', price: 990, ku: false, skip: false },
      { title: '読み放題の習慣本', asin: 'B0WISH0002', price: null, ku: true, skip: false },
      { title: '集中の技法', asin: 'B0WISH0003', price: 500, ku: false, skip: true },
    ];
    const fetchImpl = async (url) => {
      const q = decodeURIComponent(new URL(url).searchParams.get('q') || '');
      return new Response(JSON.stringify({ items: q.includes('習慣') ? [vol('a', '集中の技法'), vol('b', '習慣の科学')] : [vol('c', '哲学の入口')] }));
    };
    const llm = createLlmClient({ baseUrl: fake.url, chatModel: 'fake-chat' });
    const recs = await recommendBooks({ library: lib, analysis, llm, fetchImpl, wishlist });
    const titles = recs.map((r) => r.title);
    assert.ok(!titles.includes('集中の技法'), '購入済み・読んだ本は書誌 DB で見つかっても出さない');
    const fromWish = recs.filter((r) => r.wishlist);
    assert.equal(fromWish.length, 1, '欲しい本由来の本が混ざる（偽 LLM は 2 番目の候補を選ぶ）');
    assert.ok(fromWish[0].reason);
    assert.ok(['B0WISH0001', 'B0WISH0002'].includes(fromWish[0].wishlist.asin));
    assert.ok('price' in fromWish[0].wishlist && 'ku' in fromWish[0].wishlist);
    const pick = fake.calls.bodies.find((b) => b.response_format?.json_schema?.name === 'picks').messages[1].content;
    assert.match(pick, /欲しい本/);
    assert.doesNotMatch(pick, /『集中の技法』/);
  } finally {
    await fake.close();
  }
});

test('おすすめ: 書誌 DB に届かなくても欲しい本の候補から選べる', async () => {
  const fake = await startFakeLlm();
  try {
    const lib = emptyLibrary();
    mergeParsed(lib, SAMPLE_BOOKS);
    const wishlist = [
      { title: '習慣を続ける本', asin: 'B0WISH0001', price: 990, ku: false, skip: false },
      { title: '問いを立てる本', asin: 'B0WISH0002', price: 1200, ku: false, skip: false },
    ];
    const llm = createLlmClient({ baseUrl: fake.url, chatModel: 'fake-chat' });
    const recs = await recommendBooks({ library: lib, analysis, llm, wishlist, fetchImpl: async () => { throw new Error('offline'); } });
    assert.ok(recs.some((r) => r.wishlist), '欲しい本から選ぶ');
  } finally {
    await fake.close();
  }
});
