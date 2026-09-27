import { test } from 'node:test';
import assert from 'node:assert/strict';
import { cleanupSyncedMarks, collectMarks, filterWishlist, findWishlistBook, formatPrice, loadMarks, marksFile, memoryStore, parseWishlist, saveMarks, searchWishlist, titleKey, toggleMark } from '../web/core/wishlist.js';

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
  assert.deepEqual(w.books[0], { asin: 'B0AAAAAAA1', title: '欲しい本', price: 900, ku: false, wanted: true, purchased: false, saved: { tag: '', rating: '', kind: 'book' }, index: 0 });
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

test('filterWishlist: タグ・種別の絞り込み', () => {
  const store = memoryStore({ 'book-tag:B0AAAAAAA1': 'unwanted', 'book-tag:B0AAAAAAA3': 'seen', 'book-rating:B0AAAAAAA3': '5' });
  assert.equal(asins(filterWishlist(items(store), { tag: 'hide-unwanted' })), '234');
  assert.equal(asins(filterWishlist(items(store), { tag: 'unwanted' })), '1');
  assert.equal(asins(filterWishlist(items(store), { tag: 'untagged' })), '24');
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
