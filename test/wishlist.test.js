import { test } from 'node:test';
import assert from 'node:assert/strict';
import { applyImportedMarks, cleanupSyncedMarks, collectMarks, filterWishlist, findWishlistBook, formatPrice, inShelf, loadMarks, priceChange, priceTotal, shelfCounts, marksFile, memoryStore, parseMarksFile, openWishlistFilters, parseWishlist, saveMarks, searchWishlist, tagCounts, titleKey, toggleMark, wishlistSummary } from '../web/core/wishlist.js';

test('titleKey: 括弧のレーベル・版表記と記号・空白を落とす', () => {
  assert.equal(titleKey('731―石井四郎と細菌戦部隊の闇を暴く―（新潮文庫）'), titleKey('731 石井四郎と細菌戦部隊の闇を暴く'));
  assert.equal(titleKey('ＦＡＣＴＦＵＬＮＥＳＳ (日経BP)'), 'factfulness');
  assert.equal(titleKey('サピエンス全史（上）【合本版】'), 'サピエンス全史');
});

test('findWishlistBook: 書名の一致・前方一致で欲しい本を探す（短い書名は完全一致だけ）', () => {
  const { books } = parseWishlist({ format: 'kindle-wishlist', version: 1, books: [
    { asin: 'B0AAAAAAA1', title: '思考の整理学 (ちくま文庫)' },
    { asin: 'B0AAAAAAA2', title: 'ファスト&スロー あなたの意思はどのように決まるか? 上' },
    { asin: 'B0AAAAAAA3', title: '夜' },
  ] });
  assert.deepEqual(findWishlistBook(books, '思考の整理学'), { book: books[0], exact: true });
  assert.deepEqual(findWishlistBook(books, 'ファスト&スロー'), { book: books[1], exact: false }, '前方一致は exact: false（続編・派生本の可能性があるので呼び出し側で表示を分ける）');
  assert.equal(findWishlistBook(books, '夜')?.book.asin, 'B0AAAAAAA3', '完全一致なら短くても見つかる');
  assert.equal(findWishlistBook(books, '夜と霧'), undefined, '短い書名 "夜" を前方一致に使わない');
  assert.equal(findWishlistBook(books, '思考'), undefined, '4 文字以下の書名で前方一致しない');
  assert.equal(findWishlistBook(books, ''), undefined);
  assert.deepEqual(findWishlistBook(books, ['ファスト&スロー', '思考の整理学']), { book: books[0], exact: true }, '候補の書名を複数渡すと、どれかの完全一致を前方一致より優先する');
});

test('filterWishlist と searchWishlist は同じ正規化で当たる（全角英数字の書名・全角の語）', () => {
  const { books } = parseWishlist({ format: 'kindle-wishlist', version: 1, books: [{ asin: 'B0AAAAAAA1', title: 'ＡＩ時代の仕事術' }, { asin: 'B0AAAAAAA2', title: '別の本' }] });
  const items = books.map((book) => ({ book, marks: loadMarks(book, memoryStore()) }));
  for (const q of ['ai', 'ＡＩ 仕事']) {
    assert.deepEqual(searchWishlist(books, q).map((b) => b.asin), ['B0AAAAAAA1'], `searchWishlist: ${q}`);
    assert.deepEqual(filterWishlist(items, { q }).items.map((i) => i.book.asin), ['B0AAAAAAA1'], `filterWishlist: ${q}`);
  }
});

test('searchWishlist: 空白で AND・書名と ASIN・#タグ語は無視', () => {
  const { books } = parseWishlist({ format: 'kindle-wishlist', version: 1, books: [
    { asin: 'B0AAAAAAA1', title: 'すごい本 上' },
    { asin: 'B0AAAAAAA2', title: 'すごい本 下' },
  ] });
  assert.deepEqual(searchWishlist(books, 'すごい 上').map((b) => b.asin), ['B0AAAAAAA1']);
  assert.deepEqual(searchWishlist(books, 'ｂ0aaaaaaa2').map((b) => b.asin), ['B0AAAAAAA2'], '全角・小文字でも当たる');
  assert.deepEqual(searchWishlist(books, 'すごい #習慣').map((b) => b.asin), ['B0AAAAAAA1', 'B0AAAAAAA2']);
  assert.deepEqual(searchWishlist(books, '#習慣'), [], '#タグだけの検索では出さない');
  assert.deepEqual(searchWishlist(books, '  '), []);
});

const book = (over = {}) => ({ asin: 'B0AAAAAAA1', title: '欲しい本', price: 900, ku: false, wanted: true, purchased: false, kind: 'book', tag: '', rating: null, scraped_at: '2026-01-01T00:00:00', ...over });
const data = (books, over = {}) => ({ format: 'kindle-wishlist', version: 1, last_scraped: '2026-01-02T03:04:05', books, ...over });

test('parseWishlist: 形式を確かめて正規化する', () => {
  const w = parseWishlist(data([book(), book({ asin: 'B0AAAAAAA2', ku: true, price: null, tag: 'seen', rating: 4, kind: 'manga' })]));
  assert.equal(w.lastScraped, '2026-01-02T03:04:05');
  assert.equal(w.books.length, 2);
  assert.deepEqual(w.books[0], { asin: 'B0AAAAAAA1', title: '欲しい本', price: 900, ku: false, wanted: true, purchased: false, saved: { tag: '', rating: '', kind: 'book' }, trend: { prev: null, changedAt: null, low: null }, scrapedAt: '2026-01-01T00:00:00', history: [], index: 0 });
  assert.deepEqual(w.books[1].saved, { tag: 'seen', rating: '4', kind: 'manga' });
});

test('parseWishlist: 形式違い・版違いは理由つきで失敗する', () => {
  assert.throws(() => parseWishlist({}), /欲しい本のデータ/);
  assert.throws(() => parseWishlist(data([], { version: 2 })), /版/);
  assert.throws(() => parseWishlist('<!doctype html>'), /欲しい本のデータ/);
});

test('parseWishlist: 不正な値は捨てる（ASIN の形・価格・タグ・★）', () => {
  const w = parseWishlist(data([book({ asin: 'bad', price: -1, tag: '<x>', rating: 9, kind: 'evil' }), book({ asin: 'B0AAAAAAA3', tag: 'wanted', rating: 3 })]));
  assert.equal(w.books[0].asin, '');
  assert.equal(w.books[0].price, null);
  assert.deepEqual(w.books[0].saved, { tag: '', rating: '', kind: 'book' });
  assert.equal(w.books[1].saved.rating, '', '★は「見た」のときだけ');
});

test('parseWishlist: 値動き（前回価格・変わった日時・最安値）は今の価格がある本だけ読む', () => {
  const trend = { price_prev: 1200, price_changed_at: '2026-09-30T09:00:00', price_low: 900 };
  const w = parseWishlist(data([
    book({ ...trend }),
    book({ asin: 'B0AAAAAAA2', price: null, ku: true, ...trend }),
    book({ asin: 'B0AAAAAAA3', price_prev: 'x', price_changed_at: 5, price_low: -1 }),
  ]));
  assert.deepEqual(w.books[0].trend, { prev: 1200, changedAt: '2026-09-30T09:00:00', low: 900 });
  assert.equal(w.books[1].trend, null, 'KU・価格なしは比べられない');
  const ku = parseWishlist(data([book({ ku: true, price: 700, ...trend })])).books[0];
  assert.equal(ku.price, null, 'KU の本は価格があっても数えない（画面は Kindle Unlimited 対象と出す）');
  assert.equal(ku.trend, null);
  assert.deepEqual(w.books[2].trend, { prev: null, changedAt: null, low: null }, '不正な値は捨てる');
});

test('priceChange: 前回から値下がり・値上がりした額と、記録上の最安値かどうか', () => {
  const at = '2026-09-30T09:00:00';
  assert.deepEqual(priceChange({ price: 900, trend: { prev: 1200, changedAt: at, low: 900 } }), { diff: -300, changedAt: at, lowest: true });
  assert.deepEqual(priceChange({ price: 1500, trend: { prev: 1300, changedAt: at, low: 1100 } }), { diff: 200, changedAt: at, lowest: false });
  assert.equal(priceChange({ price: 900, trend: { prev: null, changedAt: null, low: 900 } }), null, '一度も変わっていなければ出さない');
  assert.equal(priceChange({ price: null, trend: null }), null);
  assert.equal(priceChange({ price: 900, trend: null }), null, '古いデータ（値動きの項目なし）');
  assert.deepEqual(priceChange({ price: 900, trend: { prev: 900, changedAt: at, low: 900 } }), { diff: 0, changedAt: at, lowest: true }, '差が 0 でも最安値は判定する');
});

test('loadMarks: ブラウザに保存したタグ・★・種別が公開データより優先される（旧画面と同じキー）', () => {
  const [b] = parseWishlist(data([book({ tag: 'wanted' })])).books;
  const store = memoryStore({ 'book-tag:B0AAAAAAA1': 'seen', 'book-rating:B0AAAAAAA1': '5', 'book-kind:B0AAAAAAA1': 'manga' });
  assert.deepEqual(loadMarks(b, store), { tag: 'seen', rating: '5', kind: 'manga' });
  assert.deepEqual(loadMarks(b, memoryStore()), { tag: 'wanted', rating: '', kind: 'book' });
  assert.deepEqual(loadMarks(b, memoryStore({ 'book-tag:B0AAAAAAA1': '' })), { tag: '', rating: '', kind: 'book' }, '外したタグも保存値が勝つ');
  assert.deepEqual(loadMarks(b, memoryStore({ 'book-tag:B0AAAAAAA1': 'bogus', 'book-kind:B0AAAAAAA1': 'x' })), { tag: 'wanted', rating: '', kind: 'book' });
  assert.equal(loadMarks(b, memoryStore({ 'book-rating:B0AAAAAAA1': '3' })).rating, '', '★は「見た」のときだけ');
});

test('toggleMark: タグ・★・種別の付け外し', () => {
  const m = { tag: '', rating: '', kind: 'book' };
  assert.deepEqual(toggleMark(m, { tag: 'seen' }), { marks: { tag: 'seen', rating: '', kind: 'book' }, group: 'tag' });
  assert.deepEqual(toggleMark({ ...m, tag: 'seen', rating: '3' }, { tag: 'seen' }).marks, m, '同じタグで外すと★も消える');
  assert.deepEqual(toggleMark({ ...m, tag: 'seen', rating: '3' }, { tag: 'wanted' }).marks.rating, '');
  assert.deepEqual(toggleMark({ ...m, tag: 'seen' }, { rating: '4' }).marks.rating, '4');
  assert.deepEqual(toggleMark({ ...m, tag: 'seen', rating: '4' }, { rating: '4' }).marks.rating, '', '同じ★で取り消し');
  assert.deepEqual(toggleMark(m, { kind: true }), { marks: { ...m, kind: 'manga' }, group: 'kind' });
});

test('saveMarks: 旧画面と同じキーに書き、押した時刻を残す', () => {
  const store = memoryStore();
  saveMarks(store, 'B0AAAAAAA1', { tag: 'seen', rating: '4', kind: 'manga' }, 'tag', 1000);
  assert.deepEqual(store.dump(), { 'book-tag:B0AAAAAAA1': 'seen', 'book-rating:B0AAAAAAA1': '4', 'book-mark-at:B0AAAAAAA1': '1000' });
  saveMarks(store, 'B0AAAAAAA1', { tag: 'seen', rating: '4', kind: 'manga' }, 'kind', 2000);
  assert.equal(store.get('book-kind:B0AAAAAAA1'), 'manga');
  assert.equal(store.get('book-mark-at:B0AAAAAAA1'), '2000');
});

test('cleanupSyncedMarks: 書き出し済みで公開データに追いついた保存値だけ消す', () => {
  const [b] = parseWishlist(data([book({ tag: 'wanted' })])).books;
  const synced = memoryStore({ 'book-tag:B0AAAAAAA1': 'wanted', 'book-rating:B0AAAAAAA1': '', 'book-mark-at:B0AAAAAAA1': '100', 'book-marks-exported-at': '200' });
  assert.equal(cleanupSyncedMarks(synced, b), true);
  assert.deepEqual(synced.dump(), { 'book-marks-exported-at': '200' });
  const unexported = memoryStore({ 'book-tag:B0AAAAAAA1': 'wanted', 'book-mark-at:B0AAAAAAA1': '300', 'book-marks-exported-at': '200' });
  assert.equal(cleanupSyncedMarks(unexported, b), false);
  const differs = memoryStore({ 'book-tag:B0AAAAAAA1': 'seen', 'book-mark-at:B0AAAAAAA1': '100', 'book-marks-exported-at': '200' });
  assert.equal(cleanupSyncedMarks(differs, b), false);
});

function items(store = memoryStore()) {
  const w = parseWishlist(data([
    book({ asin: 'B0AAAAAAA1', title: 'すごい本 上', price: 1200, wanted: true }),
    book({ asin: 'B0AAAAAAA2', title: 'まんが 1巻', price: null, ku: true, wanted: false, kind: 'manga' }),
    book({ asin: 'B0AAAAAAA3', title: 'あの本', price: 500, wanted: false, purchased: true }),
    book({ asin: 'B0AAAAAAA4', title: '価格なし', price: null, wanted: true }),
  ]));
  return w.books.map((b) => ({ book: b, marks: loadMarks(b, store) }));
}
const asins = (r) => r.items.map((i) => i.book.asin.slice(-1)).join('');

test('filterWishlist: 分類・検索（空白で AND・ASIN も対象）', () => {
  assert.equal(asins(filterWishlist(items(), {})), '1234');
  assert.equal(asins(filterWishlist(items(), { shelf: 'wanted' })), '14');
  assert.equal(asins(filterWishlist(items(), { shelf: 'purchased' })), '3');
  assert.equal(asins(filterWishlist(items(), { q: 'すごい 上' })), '1');
  assert.equal(asins(filterWishlist(items(), { q: 'すごい 下' })), '');
  assert.equal(asins(filterWishlist(items(), { q: 'b0aaaaaaa3' })), '3');
});

test('filterWishlist: KU・価格帯（価格なしは除外）・逆転した価格帯は無視して知らせる', () => {
  assert.equal(asins(filterWishlist(items(), { ku: true })), '2');
  assert.equal(asins(filterWishlist(items(), { min: '600' })), '1');
  assert.equal(asins(filterWishlist(items(), { max: '600' })), '3');
  const r = filterWishlist(items(), { min: '1000', max: '100' });
  assert.equal(r.priceRangeInvalid, true);
  assert.equal(asins(r), '1234');
});

test('filterWishlist: タグ・種別の絞り込み（廃止した「読みたくない」はタグなしとして扱う）', () => {
  const store = memoryStore({ 'book-tag:B0AAAAAAA1': 'unwanted', 'book-tag:B0AAAAAAA3': 'seen', 'book-rating:B0AAAAAAA3': '5' });
  assert.equal(asins(filterWishlist(items(store), { tag: 'seen' })), '3');
  assert.equal(asins(filterWishlist(items(store), { tag: 'hide-unwanted' })), '1234', '廃止した選択肢は「すべて」と同じ');
  assert.equal(asins(filterWishlist(items(store), { tag: 'untagged' })), '124');
  assert.equal(asins(filterWishlist(items(store), { kind: 'manga' })), '2');
  assert.equal(asins(filterWishlist(items(store), { kind: 'book' })), '134');
});

test('filterWishlist: 並べ替え（価格なしは常に後ろ・評価は★→見た→その他）', () => {
  assert.equal(asins(filterWishlist(items(), { sort: 'price-asc' })), '3124');
  assert.equal(asins(filterWishlist(items(), { sort: 'price-desc' })), '1324');
  assert.equal(asins(filterWishlist(items(), { sort: 'title' })), '3124');
  const store = memoryStore({ 'book-tag:B0AAAAAAA2': 'seen', 'book-tag:B0AAAAAAA4': 'seen', 'book-rating:B0AAAAAAA4': '3' });
  assert.equal(asins(filterWishlist(items(store), { sort: 'rating' })), '4213');
});

test('filterWishlist: スクレイピングの最新順（取得日時が新しい順・日時なしは後ろ）', () => {
  const w = parseWishlist(data([
    book({ asin: 'B0AAAAAAA1', scraped_at: '2026-09-23T12:00:00' }),
    book({ asin: 'B0AAAAAAA2', scraped_at: null }),
    book({ asin: 'B0AAAAAAA3', scraped_at: '2026-10-01T08:00:00' }),
    book({ asin: 'B0AAAAAAA4', scraped_at: '2026-09-25T09:00:00' }),
  ]));
  const list = w.books.map((b) => ({ book: b, marks: loadMarks(b, memoryStore()) }));
  assert.equal(asins(filterWishlist(list, { sort: 'scraped-desc' })), '3412');
});

test('parseWishlist: スクレイピングの履歴（price_history）を読み、不正な行は捨てる', () => {
  const history = [
    { at: '2026-09-20T10:00:00', price: 1200, ku: false },
    { at: '2026-09-25T10:00:00', price: null, ku: true },
    { at: 5, price: 900 },
    { at: '2026-09-30T10:00:00', price: -3, ku: 'x' },
    null,
  ];
  const [b] = parseWishlist(data([book({ price_history: history })])).books;
  assert.deepEqual(b.history, [
    { at: '2026-09-20T10:00:00', price: 1200, ku: false },
    { at: '2026-09-25T10:00:00', price: null, ku: true },
    { at: '2026-09-30T10:00:00', price: null, ku: false },
  ]);
  assert.deepEqual(parseWishlist(data([book({ price_history: 'bad' })])).books[0].history, [], '古いデータ（履歴なし）は空');
});

test('loadMarks: 廃止した「読みたくない」タグは無視する', () => {
  const [b] = parseWishlist(data([book({ tag: 'unwanted' })])).books;
  assert.equal(b.saved.tag, '');
  assert.equal(loadMarks(b, memoryStore({ 'book-tag:B0AAAAAAA1': 'unwanted' })).tag, '');
  const [wanted] = parseWishlist(data([book({ tag: 'wanted' })])).books;
  assert.equal(loadMarks(wanted, memoryStore({ 'book-tag:B0AAAAAAA1': 'unwanted' })).tag, '', 'このブラウザで外した公開データのタグを復活させない');
});

test('parseMarksFile: 廃止した「読みたくない」はタグなしとして読み込む', () => {
  const { entries } = parseMarksFile({ format: 'kindle-marks', version: 1, items: [{ asin: 'B0CNT00001', tag: 'unwanted', rating: 3 }] });
  assert.deepEqual(entries, [{ asin: 'B0CNT00001', tag: '', rating: '' }]);
});

test('inShelf / shelfCounts: 「購入済み」タグを付けた本は購入済みに移り、読みたいから外れる', () => {
  const store = memoryStore({ 'book-tag:B0AAAAAAA1': 'purchased' });
  const list = items(store);
  assert.equal(asins(filterWishlist(list, { shelf: 'purchased' })), '13');
  assert.equal(asins(filterWishlist(list, { shelf: 'wanted' })), '4');
  assert.deepEqual(shelfCounts(list), { all: 4, wanted: 1, purchased: 2 });
  assert.deepEqual(shelfCounts(items()), { all: 4, wanted: 2, purchased: 1 }, 'タグが無ければ公開データどおり');
  assert.equal(inShelf(list[0], 'all'), true);
});

test('priceTotal: 表示中の本の合計金額（KU・価格なしは数えず、その冊数も返す）', () => {
  assert.deepEqual(priceTotal(items()), { total: 1700, priced: 2, unpriced: 2 });
  assert.deepEqual(priceTotal([]), { total: 0, priced: 0, unpriced: 0 });
});

test('formatPrice: KU・価格なし・通常', () => {
  assert.equal(formatPrice({ ku: true, price: null }), 'Kindle Unlimited 対象');
  assert.equal(formatPrice({ ku: false, price: null }), '価格情報なし');
  assert.equal(formatPrice({ ku: false, price: 1234 }), '¥1,234');
});

test('collectMarks / marksFile: 押した項目だけを kindle-marks v1 で書き出す', () => {
  const store = memoryStore({ 'book-tag:B0AAAAAAA1': 'seen', 'book-rating:B0AAAAAAA1': '4', 'book-mark-at:B0AAAAAAA1': '500', 'book-kind:B0AAAAAAA2': 'book', 'book-mark-at:B0AAAAAAA2': '50', 'book-marks-exported-at': '100' });
  const s = collectMarks(items(store), store);
  assert.deepEqual([s.seen, s.rated, s.unexported], [1, 1, 1]);
  assert.deepEqual(s.items, [
    { asin: 'B0AAAAAAA1', title: 'すごい本 上', tag: 'seen', rating: 4 },
    { asin: 'B0AAAAAAA2', title: 'まんが 1巻', kind: 'book' },
  ]);
  const f = marksFile(s.items, new Date(2026, 8, 27, 7, 5));
  assert.equal(f.name, 'kindle-marks-20260927-0705.json');
  assert.deepEqual(Object.keys(f.data), ['format', 'version', 'exported_at', 'items']);
  assert.equal(f.data.format, 'kindle-marks');
  assert.equal(f.data.version, 1);
});

test('collectMarks: 保存できないブラウザでは公開データとの差を書き出す', () => {
  const list = items();
  list[0].marks = { ...list[0].marks, tag: 'wanted' };
  const s = collectMarks(list, memoryStore(), { canStore: false });
  assert.deepEqual(s.items, [{ asin: 'B0AAAAAAA1', title: 'すごい本 上', tag: 'wanted', rating: null }]);
  assert.equal(s.unexported, 1);
});

test('wishlistSummary: ホームに出す件数と Kindle Unlimited で読める本（購入済み・読んだを除き、読みたいを先頭に最大 3 冊）', () => {
  const { books } = parseWishlist({ format: 'kindle-wishlist', version: 1, books: [
    { asin: 'B0AAAAAAA1', title: 'KU 1', ku: true },
    { asin: 'B0AAAAAAA2', title: '通常', price: 990 },
    { asin: 'B0AAAAAAA3', title: 'KU 購入済み', ku: true, purchased: true },
    { asin: 'B0AAAAAAA4', title: 'KU 読みたくない', ku: true },
    { asin: 'B0AAAAAAA5', title: 'KU 見た', ku: true, tag: 'seen', rating: 4 },
    { asin: 'B0AAAAAAA6', title: 'KU 2', ku: true },
    { asin: 'B0AAAAAAA7', title: 'KU 読みたい（公開データ）', ku: true, wanted: true },
    { asin: 'B0AAAAAAA8', title: 'KU 3', ku: true },
    { asin: 'B0AAAAAAA9', title: 'KU 購入済み（このブラウザのタグ）', ku: true },
  ] });
  const store = memoryStore({ 'book-tag:B0AAAAAAA4': 'unwanted', 'book-tag:B0AAAAAAA9': 'purchased', 'book-tag:B0AAAAAAA8': 'wanted' });
  const items = books.map((book) => ({ book, marks: loadMarks(book, store) }));
  const s = wishlistSummary(items);
  assert.equal(s.total, 9);
  // 件数は「KU のみ」で絞り込んだ欲しい本の画面（#/wishlist?ku=1）と同じ数にする
  assert.equal(s.kuCount, filterWishlist(items, { ku: true }).items.length);
  assert.equal(s.kuCount, 8);
  assert.deepEqual(s.picks.map((b) => b.asin), ['B0AAAAAAA7', 'B0AAAAAAA8', 'B0AAAAAAA1']);
  assert.deepEqual(wishlistSummary([]), { total: 0, kuCount: 0, picks: [] });
});

const TAGGED = { format: 'kindle-wishlist', version: 1, books: [
  { asin: 'B0CNT00001', title: '本1', tag: 'unwanted' },
  { asin: 'B0CNT00002', title: '本2', tag: 'seen', rating: 4 },
  { asin: 'B0CNT00003', title: '本3' },
  { asin: 'B0CNT00004', title: '本4', tag: 'wanted' },
] };

test('tagCounts: タグの絞り込みの選択肢ごとに、選ぶと出る件数を返す', () => {
  const { books } = parseWishlist(TAGGED);
  const items = books.map((book) => ({ book, marks: loadMarks(book, memoryStore({ 'book-tag:B0CNT00003': 'purchased' })) }));
  const counts = tagCounts(items);
  assert.deepEqual(counts, { all: 4, wanted: 1, purchased: 1, seen: 1, untagged: 1 }, '廃止した「読みたくない」の本はタグなし');
  for (const [filter, n] of Object.entries(counts)) {
    assert.equal(filterWishlist(items, { tag: filter }).items.length, n, `絞り込みの結果と件数が一致する: ${filter}`);
  }
});

test('parseMarksFile: 書き出しファイル（kindle-marks v1）を確かめ、使える項目だけ返す', () => {
  const file = marksFile([
    { asin: 'B0CNT00001', title: '本1', tag: 'seen', rating: 5 },
    { asin: 'B0CNT00002', title: '本2', kind: 'manga' },
    { asin: 'B0CNT00003', title: '本3', tag: '', rating: null },
    { asin: 'B0CNT00004', title: '本4', tag: 'wanted', rating: 3 },
    { asin: 'bad', tag: 'seen' },
    { asin: 'B0CNT00005', tag: 'bogus', kind: 'novel' },
  ]).data;
  assert.deepEqual(parseMarksFile(JSON.parse(JSON.stringify(file))).entries, [
    { asin: 'B0CNT00001', tag: 'seen', rating: '5' },
    { asin: 'B0CNT00002', kind: 'manga' },
    { asin: 'B0CNT00003', tag: '', rating: '' },
    { asin: 'B0CNT00004', tag: 'wanted', rating: '' },
  ]);
  assert.equal(parseMarksFile(file).exportedAt, Date.parse(file.exported_at), '書き出した時刻も返す（端末側の新しい変更を上書きしないため）');
  assert.equal(parseMarksFile({ format: 'kindle-marks', version: 1, items: [] }).exportedAt, 0);
  assert.throws(() => parseMarksFile({ format: 'kindle-wishlist', version: 1, books: [] }), /書き出したタグのファイル/);
  assert.throws(() => parseMarksFile(null), /書き出したタグのファイル/);
  assert.throws(() => parseMarksFile({ format: 'kindle-marks', version: 2, items: [] }), /版（2）/);
});

test('applyImportedMarks: 読み込んだタグ・★・種別をブラウザに保存し、書き出すと同じ内容に戻る（端末の移し替え）', () => {
  const { books } = parseWishlist(TAGGED);
  // 移し元の端末
  const from = memoryStore();
  const fromItems = books.map((book) => ({ book, marks: loadMarks(book, from) }));
  saveMarks(from, 'B0CNT00001', { tag: 'seen', rating: '5', kind: 'book' }, 'tag', 1);
  saveMarks(from, 'B0CNT00003', { tag: '', rating: '', kind: 'manga' }, 'kind', 1);
  for (const item of fromItems) item.marks = loadMarks(item.book, from);
  const file = JSON.parse(JSON.stringify(marksFile(collectMarks(fromItems, from).items).data));

  // 移し先の端末（何も保存していない）。一覧に無い本は飛ばす
  file.items.push({ asin: 'B0NOTLIST1', title: '一覧に無い本', tag: 'wanted', rating: null });
  const to = memoryStore();
  const toItems = books.map((book) => ({ book, marks: loadMarks(book, to) }));
  const result = applyImportedMarks(toItems, to, parseMarksFile(file), 2);
  assert.deepEqual(result, { applied: 2, skipped: 1, kept: 0 });
  assert.deepEqual(toItems[0].marks, { tag: 'seen', rating: '5', kind: 'book' });
  assert.equal(toItems[2].marks.kind, 'manga');
  assert.deepEqual(toItems[1].marks, { tag: 'seen', rating: '4', kind: 'book' }, 'ファイルに無い本はそのまま');
  assert.equal(to.get('book-tag:B0NOTLIST1'), null);
  // 画面を開き直しても（保存値から読み直しても）同じ
  assert.deepEqual(loadMarks(books[0], to), { tag: 'seen', rating: '5', kind: 'book' });
  assert.deepEqual(collectMarks(toItems, to).items, collectMarks(fromItems, from).items);
});

test('applyImportedMarks: ファイルを書き出した後にこの端末で変えた本は上書きしない（書き出していない唯一の変更を消さない）', () => {
  const { books } = parseWishlist(TAGGED);
  const store = memoryStore();
  const items = books.map((book) => ({ book, marks: loadMarks(book, store) }));
  const exportedAt = Date.parse('2026-10-01T00:00:00Z');
  // 本3: ファイルより後に「見た ★5」を付けた / 本4: ファイルより前に変えた
  saveMarks(store, 'B0CNT00003', { tag: 'seen', rating: '5', kind: 'book' }, 'tag', exportedAt + 1000);
  saveMarks(store, 'B0CNT00004', { tag: 'purchased', rating: '', kind: 'book' }, 'tag', exportedAt - 1000);
  for (const item of items) item.marks = loadMarks(item.book, store);
  const file = { format: 'kindle-marks', version: 1, exported_at: new Date(exportedAt).toISOString(), items: [
    { asin: 'B0CNT00003', tag: 'wanted', rating: null },
    { asin: 'B0CNT00004', tag: 'wanted', rating: null },
  ] };
  const result = applyImportedMarks(items, store, parseMarksFile(file), exportedAt + 5000);
  assert.deepEqual(result, { applied: 1, skipped: 0, kept: 1 });
  assert.deepEqual(loadMarks(books[2], store), { tag: 'seen', rating: '5', kind: 'book' }, '新しい方（端末）が残る');
  assert.equal(items[2].marks.tag, 'seen');
  assert.equal(loadMarks(books[3], store).tag, 'wanted', '古い方（端末）はファイルで戻す');
});

test('openWishlistFilters: リンクから開いた条件は描き直しで戻さず、普通に開き直したらリンク前の条件に戻す', () => {
  const normal = { shelf: 'wanted', q: '自分の語', sort: 'price-asc', ku: false, min: '100', max: '900', tag: 'seen', kind: 'manga' };
  // ホームの「Kindle Unlimited 対象をすべて見る」から開く: KU だけで絞り込み、それまでの条件は取っておく
  let s = openWishlistFilters(normal, null, { ku: true });
  assert.deepEqual(s.filters, { shelf: 'all', reading: 'all', q: '', sort: 'default', ku: true, min: '', max: '', tag: 'all', kind: 'all' });
  assert.equal(s.normal, normal);
  // リンク先で利用者が条件を変えた後、同期などで同じ画面を描き直しても変えた条件のまま
  const changed = { ...s.filters, shelf: 'wanted', q: '猫' };
  s = openWishlistFilters(changed, s.normal, { ku: true, refresh: true });
  assert.equal(s.filters, changed);
  assert.equal(s.normal, normal);
  // 別の画面から同じリンクをもう一度押したら、リンクの条件で開き直す（リンクに出した件数と合わせる）
  s = openWishlistFilters(changed, s.normal, { ku: true });
  assert.equal(s.filters.q, '');
  assert.equal(s.filters.shelf, 'all');
  assert.equal(s.normal, normal, 'リンクを続けて開いても、取っておくのは最初のリンクの前の条件');
  // 検索のリンクに移っても同じ
  s = openWishlistFilters(s.filters, s.normal, { q: '本' });
  assert.equal(s.filters.q, '本');
  assert.equal(s.filters.ku, false);
  // 普通に開き直すと、リンク前の条件にそのまま戻る
  assert.deepEqual(openWishlistFilters(s.filters, s.normal, {}), { filters: normal, normal: null });
  // 普通に開いているときは条件を変えない（描き直しでも同じ）
  assert.deepEqual(openWishlistFilters(normal, null, {}), { filters: normal, normal: null });
  assert.deepEqual(openWishlistFilters(normal, null, { refresh: true }), { filters: normal, normal: null });
});

test('filterWishlist: 値下がり額が大きい順（値下がりした本が先・値上がり / 変化なし / 価格なしは元の順で後ろ）', () => {
  const w = parseWishlist(data([
    book({ asin: 'B0AAAAAAA1', title: '値上がり', price: 1500, price_prev: 1200 }),
    book({ asin: 'B0AAAAAAA2', title: '少し値下がり', price: 900, price_prev: 1000 }),
    book({ asin: 'B0AAAAAAA3', title: '変化なし', price: 800 }),
    book({ asin: 'B0AAAAAAA4', title: '大きく値下がり', price: 500, price_prev: 1500 }),
    book({ asin: 'B0AAAAAAA5', title: 'KU', price: null, ku: true }),
    book({ asin: 'B0AAAAAAA6', title: '同じ額の値下がり', price: 400, price_prev: 500 }),
  ]));
  const list = w.books.map((b) => ({ book: b, marks: loadMarks(b, memoryStore()) }));
  assert.equal(asins(filterWishlist(list, { sort: 'price-drop' })), '426135', '同じ下げ幅は元の順');
});

test('積読: 購入済みの本を本棚と ASIN（無ければ書名）で照合し、「まだ線が無い」「読書中」に分けて絞り込む', async () => {
  const { readingLookup, readingCounts } = await import('../web/core/wishlist.js');
  const wl = parseWishlist({
    format: 'kindle-wishlist',
    version: 1,
    books: [
      { asin: 'B0RD000001', title: '線を引いた本', purchased: true },
      { asin: 'B0RD000002', title: '積んだ本', purchased: true },
      { asin: 'B0RD000003', title: '書名で一致する本（新潮文庫）', purchased: true },
      { asin: 'B0RD000004', title: '欲しいだけの本', wanted: true },
    ],
  });
  // listBooks の結果（線のある本だけ）
  const shelf = [
    { asin: 'B0RD000001', title: '別の書名', count: 12, lastHighlightedAt: '2026-09-30T10:00:00.000Z' },
    { asin: '', title: '書名で一致する本', count: 1, lastHighlightedAt: '2026-08-01T00:00:00.000Z' },
    { asin: 'B0RD000004', title: '欲しいだけの本', count: 3, lastHighlightedAt: '2026-07-01T00:00:00.000Z' },
  ];
  const lookup = readingLookup(shelf);
  const its = wl.books.map((book) => ({ book, marks: loadMarks(book, memoryStore()), reading: lookup(book) }));
  assert.deepEqual(its[0].reading, { count: 12, lastHighlightedAt: '2026-09-30T10:00:00.000Z' });
  assert.equal(its[1].reading, null);
  assert.equal(its[2].reading.count, 1, '本棚側に ASIN が無くても書名で照合する');
  assert.deepEqual(readingCounts(its), { all: 3, unread: 1, reading: 2 }, '数えるのは購入済みだけ');
  const titles = (f) => filterWishlist(its, f).items.map((i) => i.book.title);
  assert.deepEqual(titles({ shelf: 'purchased', reading: 'unread' }), ['積んだ本']);
  assert.deepEqual(titles({ shelf: 'purchased', reading: 'reading' }), ['線を引いた本', '書名で一致する本（新潮文庫）']);
  assert.equal(titles({ shelf: 'all', reading: 'unread' }).length, 4, '購入済み以外の分類では使わない');
});

test('Amazon の URL: おすすめの書名で Kindle ストアを検索する URL を作り、ASIN があれば商品ページにする', async () => {
  const { amazonKindleUrl } = await import('../web/core/wishlist.js');
  assert.equal(amazonKindleUrl({ title: '習慣の科学 & 実践 #1' }), 'https://www.amazon.co.jp/s?k=%E7%BF%92%E6%85%A3%E3%81%AE%E7%A7%91%E5%AD%A6%20%26%20%E5%AE%9F%E8%B7%B5%20%231&i=digital-text');
  assert.equal(amazonKindleUrl({ title: '  余白  ' }), 'https://www.amazon.co.jp/s?k=%E4%BD%99%E7%99%BD&i=digital-text');
  assert.equal(amazonKindleUrl({ title: '本', asin: 'B0H28J797V' }), 'https://www.amazon.co.jp/dp/B0H28J797V');
  // ASIN の形でない値は URL に入れず、書名の検索にする
  assert.equal(amazonKindleUrl({ title: '本', asin: '../evil?x=1' }), 'https://www.amazon.co.jp/s?k=%E6%9C%AC&i=digital-text');
  assert.equal(amazonKindleUrl({ title: '' }), '');
});

test('価格推移のグラフ: 価格のある履歴を時刻順に並べ、幅・高さに収まる座標にする', async () => {
  const { priceSparkline } = await import('../web/core/wishlist.js');
  const h = [
    { at: '2026-10-03T00:00:00Z', price: 880, ku: false },
    { at: '2026-09-29T00:00:00Z', price: 1430, ku: false },
    { at: '2026-10-01T00:00:00Z', price: null, ku: true },
    { at: '2026-10-01T00:00:00Z', price: 1210, ku: false },
  ];
  const s = priceSparkline(h, { width: 100, height: 20, pad: 2 });
  assert.deepEqual({ min: s.min, max: s.max, first: s.first, last: s.last, count: s.count }, { min: 880, max: 1430, first: 1430, last: 880, count: 3 });
  assert.deepEqual(s.points, [[2, 2], [50, 20 - 2 - ((1210 - 880) / (1430 - 880)) * 16], [98, 18]], '古い順・時刻の間隔どおり・高いほど上');
  // 価格が変わらなければ真ん中の高さの横線
  const flat = priceSparkline([{ at: '2026-10-01T00:00:00Z', price: 500 }, { at: '2026-10-02T00:00:00Z', price: 500 }], { width: 100, height: 20, pad: 2 });
  assert.deepEqual(flat.points, [[2, 10], [98, 10]]);
  // 価格のある点が 2 つ未満なら描かない
  assert.equal(priceSparkline([{ at: '2026-10-01T00:00:00Z', price: 500 }]), null);
  assert.equal(priceSparkline([]), null);
});
