// 欲しい本（kindle_system が kindle-wishlist-site に公開する wishlist.json）の読み込み・タグ・絞り込み。
// タグ・★・種別は旧画面（kindle-wishlist-site の index.html）と同じ localStorage のキーに保存する。
// 2 つのサイトは同じオリジン（nihi566.github.io）なので、旧画面で付けたタグがそのまま読める。
// 書き出しファイルも旧画面と同じ kindle-marks v1（PC の `python run.py import-marks` で取り込める）。

import { bookKey, normalizeText } from './text.js';

export const WISHLIST_FORMAT = 'kindle-wishlist';
// 「読みたくない」（unwanted）は廃止した。旧画面・公開データ・書き出しファイルに残っていても、タグなしとして扱う
export const TAG_LABELS = { wanted: '読みたい', purchased: '購入済み', seen: '読んだ' };
export const KIND_LABELS = { manga: 'マンガ', book: '本' };
export const TAG_FILTER_LABELS = { all: 'すべて', wanted: '読みたい', purchased: '購入済み', seen: '読んだ', untagged: 'タグなし' };

export const KEYS = {
  tag: 'book-tag:',
  rating: 'book-rating:',
  kind: 'book-kind:',
  at: 'book-mark-at:',
  exportedAt: 'book-marks-exported-at',
  tagFilter: 'book-tag-filter',
  kindFilter: 'book-kind-filter',
};
const MARK_FIELDS = ['tag', 'rating', 'kind'];
// 書き出し・取り込みの単位。★は「読んだ」に付くのでタグと一緒に扱う
const MARK_GROUPS = { tag: ['tag', 'rating'], kind: ['kind'] };
const ASIN = /^[A-Z0-9]{10}$/;

const RETIRED_TAG = 'unwanted';
const isTag = (t) => Object.hasOwn(TAG_LABELS, t);
const isKind = (k) => k === 'manga' || k === 'book';
// wishlist.json の price_reason のうち画面に出すもの（ku は「Kindle Unlimited 対象」、unknown は理由なしと同じ表示）
const PRICE_REASON_LABELS = {
  not_found: '販売終了の可能性',
  no_price: '販売停止・予約前の可能性',
  blocked: '取得に失敗。次の取得待ち',
  page_error: '取得に失敗。次の取得待ち',
  not_scraped: 'まだ取得していません',
};
const yen = (v) => (Number.isFinite(v) && v >= 0 ? v : null);

/** wishlist.json を確かめて、画面で使う形にする。形式が違えば理由つきで失敗する */
export function parseWishlist(data) {
  if (!data || typeof data !== 'object' || data.format !== WISHLIST_FORMAT || !Array.isArray(data.books)) {
    throw new Error('欲しい本のデータ（wishlist.json）の形式ではありません');
  }
  if (data.version !== 1) throw new Error(`欲しい本のデータの版（${data.version}）に対応していません。アプリを更新してください`);
  const books = data.books.map((b, index) => {
    const tag = isTag(b?.tag) ? b.tag : '';
    const rating = tag === 'seen' && Number.isInteger(b.rating) && b.rating >= 1 && b.rating <= 5 ? String(b.rating) : '';
    // KU の本は価格を持たない扱いにする（画面は「Kindle Unlimited 対象」と出すので、合計・値動きにも入れない）
    const price = b?.ku === true ? null : yen(b?.price);
    return {
      asin: ASIN.test(b?.asin) ? b.asin : '',
      title: String(b?.title ?? ''),
      price,
      ku: b?.ku === true,
      wanted: b?.wanted === true,
      purchased: b?.purchased === true,
      saved: { tag, rating, kind: isKind(b?.kind) ? b.kind : 'book' },
      // 値動き（kindle_system が付ける前回価格・変わった日時・最安値）。今の価格が無い本は比べられないので持たない
      trend: price === null ? null : { prev: yen(b?.price_prev), changedAt: typeof b?.price_changed_at === 'string' ? b.price_changed_at : null, low: yen(b?.price_low) },
      scrapedAt: typeof b?.scraped_at === 'string' ? b.scraped_at : null,
      history: parseHistory(b?.price_history),
      // 価格が無い理由（kindle_system の report.py が付ける。買えない本か、取り直しが要る本か）。KU・価格のある本は持たない
      priceReason: price === null && b?.ku !== true && Object.hasOwn(PRICE_REASON_LABELS, b?.price_reason) ? b.price_reason : '',
      index,
    };
  });
  return { lastScraped: typeof data.last_scraped === 'string' ? data.last_scraped : null, books };
}

/** スクレイピングの履歴（kindle_system が載せる price_history: [{ at, price, ku }]。古い順）。日時の無い行は捨てる */
function parseHistory(rows) {
  if (!Array.isArray(rows)) return [];
  return rows.filter((r) => typeof r?.at === 'string').map((r) => ({ at: r.at, price: yen(r.price), ku: r.ku === true }));
}

/** localStorage を包んだ保存先。保存できないブラウザ（サイトデータのブロック等）では canStore が false */
export function browserStore(ls = globalThis.localStorage) {
  let canStore = false;
  try {
    ls.setItem('book-marks-probe', '1');
    ls.removeItem('book-marks-probe');
    canStore = true;
  } catch {
    /* 保存できない */
  }
  return {
    canStore,
    get(key) {
      try {
        return ls.getItem(key);
      } catch {
        return null;
      }
    },
    set(key, value) {
      try {
        if (value === null) ls.removeItem(key);
        else ls.setItem(key, value);
      } catch {
        /* 保存できない */
      }
    },
  };
}

/** テスト・保存できないブラウザ用の保存先 */
export function memoryStore(initial = {}) {
  const map = new Map(Object.entries(initial));
  return {
    canStore: true,
    get: (key) => (map.has(key) ? map.get(key) : null),
    set: (key, value) => (value === null ? map.delete(key) : map.set(key, value)),
    dump: () => Object.fromEntries(map),
  };
}

/** 表示するタグ・★・種別。ブラウザに保存した値が公開データより優先される（旧画面と同じ） */
export function loadMarks(book, store) {
  const marks = { ...book.saved };
  if (book.asin) {
    const tag = store.get(KEYS.tag + book.asin);
    const rating = store.get(KEYS.rating + book.asin);
    const kind = store.get(KEYS.kind + book.asin);
    // 廃止した「読みたくない」をこのブラウザで付けていたら、公開データのタグに戻さず「タグなし」にする
    if (tag === RETIRED_TAG) marks.tag = '';
    else if (tag !== null && (tag === '' || isTag(tag))) marks.tag = tag;
    if (rating !== null && /^[1-5]?$/.test(rating)) marks.rating = rating;
    if (isKind(kind)) marks.kind = kind;
  }
  if (marks.tag !== 'seen') marks.rating = '';
  return marks;
}

/** ボタンを押したあとの状態と、保存する単位（tag / kind） */
export function toggleMark(marks, press) {
  const next = { ...marks };
  if (press.kind) {
    next.kind = marks.kind === 'manga' ? 'book' : 'manga';
    return { marks: next, group: 'kind' };
  }
  if (press.tag) {
    next.tag = marks.tag === press.tag ? '' : press.tag;
    if (next.tag !== 'seen') next.rating = '';
  } else if (press.rating) {
    next.rating = marks.rating === press.rating ? '' : press.rating;
  }
  return { marks: next, group: 'tag' };
}

/** 押した単位のキーと押した時刻を保存する。公開データと同じ値に戻したときも保存する
 *（取り込んでから公開し直すまでの間は、DB の方が公開データより新しいことがあるため。旧画面と同じ） */
export function saveMarks(store, asin, marks, group, now = Date.now()) {
  if (!asin) return;
  for (const field of MARK_GROUPS[group]) store.set(KEYS[field] + asin, marks[field]);
  store.set(KEYS.at + asin, String(now));
}

const storedAt = (store, asin) => parseInt(store.get(KEYS.at + asin), 10) || 0;
const exportedAt = (store) => parseInt(store.get(KEYS.exportedAt), 10) || 0;
const sameMarks = (a, b) => a.tag === b.tag && a.rating === b.rating && a.kind === b.kind;

/** 書き出し済みの変更が公開データに追いついたら（取り込み・再公開の後）、ブラウザの保存値を消す */
export function cleanupSyncedMarks(store, book) {
  if (!book.asin) return false;
  const stored = MARK_FIELDS.some((f) => store.get(KEYS[f] + book.asin) !== null);
  if (!stored || !sameMarks(loadMarks(book, store), book.saved) || storedAt(store, book.asin) > exportedAt(store)) return false;
  for (const f of MARK_FIELDS) store.set(KEYS[f] + book.asin, null);
  store.set(KEYS.at + book.asin, null);
  return true;
}

/**
 * 欲しい本の画面を開いたときの絞り込み。filters: いまの条件、normal: リンクから開く前の条件（普通に開いているときは null）。
 * 検索・おすすめ・ホームのリンク（q / ku）から来たときはその条件だけで開き（リンクに出した件数と合わせる）、それまでの条件を取っておく。
 * 普通に開き直したら取っておいた条件に戻す。同じ画面の描き直し（refresh: 同期のあとなど）では、利用者がリンク先で変えた条件を残す
 */
export function openWishlistFilters(filters, normal, { q = '', ku = false, refresh = false } = {}) {
  if (q || ku) {
    if (refresh && normal) return { filters, normal };
    return { filters: { ...filters, q, ku, shelf: 'all', reading: 'all', sort: 'default', min: '', max: '', tag: 'all', kind: 'all' }, normal: normal ?? filters };
  }
  return { filters: normal ?? filters, normal: null };
}

const priceValue = (v) => (v === '' || v === undefined || v === null ? NaN : parseFloat(v));

// 評価が高い順: ★の数、「読んだ」だけで★なしは★の付いた本の後、「読んだ」以外はさらに後
const ratingValue = (m) => (m.tag !== 'seen' ? 0 : parseInt(m.rating, 10) || 0.5);

function matchesTag(tag, filter) {
  if (filter === 'untagged') return tag === '';
  if (isTag(filter)) return tag === filter;
  return true;
}

/** タグの絞り込みの選択肢（TAG_FILTER_LABELS のキー）ごとに、選ぶと残る件数。items: [{ book, marks }] */
export function tagCounts(items) {
  const counts = Object.fromEntries(Object.keys(TAG_FILTER_LABELS).map((k) => [k, 0]));
  for (const { marks } of items) {
    for (const filter of Object.keys(counts)) if (matchesTag(marks.tag, filter)) counts[filter]++;
  }
  return counts;
}

/**
 * 分類（すべて/読みたい/購入済み）に入るか。「購入済み」タグを付けた本は、公開データの更新を待たずに
 * 購入済みへ移し、読みたいから外す（買った本を読みたいに残さない）
 */
export function inShelf({ book, marks }, shelf = 'all') {
  const purchased = book.purchased || marks.tag === 'purchased';
  if (shelf === 'purchased') return purchased;
  if (shelf === 'wanted') return book.wanted && !purchased;
  return true;
}

export function shelfCounts(items) {
  return Object.fromEntries(['all', 'wanted', 'purchased'].map((shelf) => [shelf, items.filter((item) => inShelf(item, shelf)).length]));
}

/**
 * 本棚（model.js の listBooks の結果。線のある本だけ）から、欲しい本の読書の状況
 * { count, lastHighlightedAt } を引く関数を作る。ASIN で照合し、無ければ書名（titleKey）で照合する。線が無ければ null
 */
export function readingLookup(shelfBooks) {
  const byAsin = new Map();
  const byTitle = new Map();
  for (const b of shelfBooks) {
    const r = { count: b.count, lastHighlightedAt: b.lastHighlightedAt };
    if (ASIN.test(b.asin)) byAsin.set(b.asin, r);
    const key = titleKey(b.title);
    if (key && !byTitle.has(key)) byTitle.set(key, r);
  }
  return (book) => byAsin.get(book.asin) || byTitle.get(titleKey(book.title)) || null;
}

/** 購入済みの本を「まだ線が無い」（unread）と「読書中」（reading）に分けたか。items: [{ book, marks, reading }] */
export function matchesReading(item, reading = 'all') {
  if (reading === 'unread') return !item.reading;
  if (reading === 'reading') return Boolean(item.reading);
  return true;
}

export function readingCounts(items) {
  const purchased = items.filter((item) => inShelf(item, 'purchased'));
  return Object.fromEntries(['all', 'unread', 'reading'].map((r) => [r, purchased.filter((item) => matchesReading(item, r)).length]));
}

/** 本の合計金額。KU・価格なしの本は数えず、その冊数を unpriced で返す */
export function priceTotal(items) {
  const priced = items.filter(({ book }) => book.price !== null);
  return { total: priced.reduce((sum, { book }) => sum + book.price, 0), priced: priced.length, unpriced: items.length - priced.length };
}

/** 前回の価格からの差（diff。負なら値下がり）と、記録上の最安値か。一度も変わっていなければ null */
export function priceChange(book) {
  const t = book.trend;
  if (book.price === null || !t || t.prev === null) return null;
  return { diff: book.price - t.prev, changedAt: t.changedAt, lowest: t.low !== null && book.price <= t.low };
}

/**
 * 価格推移の小さなグラフの座標。価格のある履歴（KU・取得できずは除く）を古い順に並べ、横は時刻の間隔、
 * 縦は価格（高いほど上）で width × height（周りに pad の余白）に収める。価格のある点が 2 つ未満なら null
 */
export function priceSparkline(history, { width = 120, height = 28, pad = 3 } = {}) {
  const rows = (history || [])
    .filter((r) => typeof r?.price === 'number' && Number.isFinite(Date.parse(r.at)))
    .map((r) => ({ t: Date.parse(r.at), price: r.price }))
    .sort((a, b) => a.t - b.t);
  if (rows.length < 2) return null;
  const prices = rows.map((r) => r.price);
  const min = Math.min(...prices);
  const max = Math.max(...prices);
  const t0 = rows[0].t;
  const span = rows[rows.length - 1].t - t0;
  const x = (r, i) => pad + (span ? (r.t - t0) / span : i / (rows.length - 1)) * (width - pad * 2);
  const y = (p) => (max === min ? height / 2 : height - pad - ((p - min) / (max - min)) * (height - pad * 2));
  return { points: rows.map((r, i) => [x(r, i), y(r.price)]), min, max, first: prices[0], last: prices[prices.length - 1], count: rows.length };
}

/**
 * items: [{ book, marks, reading? }]。f: { shelf: all|wanted|purchased, reading: all|unread|reading（購入済みのときだけ使う）, q, ku, min, max, tag, kind, sort }
 * 戻り値の priceRangeInvalid は下限 > 上限（そのときは価格帯を無視する）
 */
export function filterWishlist(items, f = {}) {
  // ハイライトの検索（searchWishlist）と同じ正規化にする（全角英数字の書名・全角の語でも件数と結果がずれない）
  const words = normalizeText(f.q).split(' ').filter(Boolean);
  let min = priceValue(f.min);
  let max = priceValue(f.max);
  const priceRangeInvalid = !Number.isNaN(min) && !Number.isNaN(max) && min > max;
  if (priceRangeInvalid) min = max = NaN;
  const kind = f.kind || 'all';
  const visible = items.filter((item) => {
    const { book, marks } = item;
    if (!inShelf(item, f.shelf)) return false;
    if (f.shelf === 'purchased' && !matchesReading(item, f.reading)) return false;
    const hay = normalizeText(`${book.title} ${book.asin}`);
    if (!words.every((w) => hay.includes(w))) return false;
    if (f.ku && !book.ku) return false;
    if (!matchesTag(marks.tag, f.tag || 'all')) return false;
    if (kind !== 'all' && marks.kind !== kind) return false;
    if (!Number.isNaN(min) && (book.price === null || book.price < min)) return false;
    if (!Number.isNaN(max) && (book.price === null || book.price > max)) return false;
    return true;
  });
  const byIndex = (a, b) => a.book.index - b.book.index;
  const sorters = {
    title: (a, b) => a.book.title.localeCompare(b.book.title, 'ja') || byIndex(a, b),
    rating: (a, b) => ratingValue(b.marks) - ratingValue(a.marks) || byIndex(a, b),
    'price-asc': (a, b) => comparePrice(a, b, 1) || byIndex(a, b),
    'price-desc': (a, b) => comparePrice(a, b, -1) || byIndex(a, b),
    'price-drop': (a, b) => dropAmount(b) - dropAmount(a) || byIndex(a, b),
    // 日時は ISO 形式の文字列なので文字列のまま比べられる。取得日時の無い本は後ろ
    'scraped-desc': (a, b) => (b.book.scrapedAt ?? '').localeCompare(a.book.scrapedAt ?? '') || byIndex(a, b),
  };
  return { items: visible.sort(sorters[f.sort] || byIndex), priceRangeInvalid };
}

// 価格なし（KU・未取得）は並べ替えの向きに関係なく後ろ
function comparePrice(a, b, dir) {
  const pa = a.book.price;
  const pb = b.book.price;
  if (pa === null || pb === null) return (pa === null) - (pb === null);
  return (pa - pb) * dir;
}

// 前回の価格からの値下がり額。値上がり・変化なし・値動きの記録が無い本は 0（並べ替えで後ろに回す）
function dropAmount({ book }) {
  const c = priceChange(book);
  return c && c.diff < 0 ? -c.diff : 0;
}

/** 書名の照合キー。括弧で囲んだレーベル・版表記（（新潮文庫）・【合本版】など）と記号・空白を落とす */
export function titleKey(title) {
  return bookKey(String(title ?? '').normalize('NFKC').replace(/[(（【［\[][^)）】］\]]*[)）】］\]]/g, ''));
}

// 前方一致に使う書名の最短の長さ（「夜」が「夜と霧」に当たるような誤一致を避ける）
const MIN_PREFIX = 5;

/**
 * 書名（候補を複数渡せる）から欲しい本を探し、{ book, exact } を返す。照合キーの一致を優先し、無ければ前方一致
 *（「ファスト&スロー」→「ファスト&スロー あなたの意思は…上」のように副題・巻数の違いを吸収する）。
 * 前方一致は続編・派生本（「7つの習慣」→「7つの習慣 ティーンズ」）にも当たるので exact: false にして、
 * 呼び出し側は「登録済み」と言い切らない
 */
export function findWishlistBook(books, titles) {
  const keys = [titles].flat().map(titleKey).filter(Boolean);
  if (!keys.length) return undefined;
  const indexed = books.map((book) => ({ book, key: titleKey(book.title) })).filter((b) => b.key);
  for (const key of keys) {
    const exact = indexed.find((b) => b.key === key);
    if (exact) return { book: exact.book, exact: true };
  }
  for (const key of keys) {
    const prefix = indexed.find((b) => (key.length >= MIN_PREFIX && b.key.startsWith(key)) || (b.key.length >= MIN_PREFIX && key.startsWith(b.key)));
    if (prefix) return { book: prefix.book, exact: false };
  }
  return undefined;
}

/** ハイライトの検索画面用。空白で区切った語をすべて含む本（書名・ASIN）。#タグの語はハイライト用なので無視する */
export function searchWishlist(books, q) {
  const words = normalizeText(q).split(' ').filter((w) => w && !w.startsWith('#'));
  if (!words.length) return [];
  return books.filter((b) => {
    const hay = normalizeText(`${b.title} ${b.asin}`);
    return words.every((w) => hay.includes(w));
  });
}

const SKIP_IN_PICKS = new Set(['purchased', 'seen']);

/**
 * ホーム用の要約。items: [{ book, marks }]。
 * kuCount は「Kindle Unlimited のみ」で絞り込んだ欲しい本の画面と同じ数（リンク先の件数と食い違わないように）。
 * picks は今すぐ読める候補: KU で、購入済み・読んだを除き、読みたいを先頭に最大 3 冊。
 */
export function wishlistSummary(items, limit = 3) {
  const ku = items.filter(({ book }) => book.ku);
  const wanted = ({ book, marks }) => book.wanted || marks.tag === 'wanted';
  const picks = ku
    .filter(({ book, marks }) => !book.purchased && !SKIP_IN_PICKS.has(marks.tag))
    .sort((a, b) => wanted(b) - wanted(a) || a.book.index - b.book.index)
    .slice(0, limit)
    .map(({ book }) => book);
  return { total: items.length, kuCount: ku.length, picks };
}

// おすすめに渡す欲しい本の上限（PC へ送る本文の大きさを抑える。公開データは 300 冊ほど）
const RECOMMEND_WISHLIST_MAX = 1000;

/**
 * おすすめの選定に渡す欲しい本 [{ title, asin, price, ku, skip }]。skip は候補から除く本（購入済み・読んだ）。
 * タグはブラウザ（localStorage）にしか無いので、分析を始めるときに Web アプリがこの形にして渡す（PC へは POST /api/analyze の本文で送る）
 */
export function toRecommendWishlist(items) {
  return wishlistForRecommend(items.map(({ book, marks }) => ({ title: book.title, asin: book.asin, price: book.price, ku: book.ku, skip: inShelf({ book, marks }, 'purchased') || marks.tag === 'seen' })));
}

/** おすすめに渡す欲しい本の形を確かめる（PC に送られてきた値は信用しない）。形の合わない項目は捨てる */
export function wishlistForRecommend(list) {
  if (!Array.isArray(list)) return [];
  return list.slice(0, RECOMMEND_WISHLIST_MAX).flatMap((b) => {
    const title = typeof b?.title === 'string' ? b.title.trim().slice(0, 200) : '';
    if (!title) return [];
    const ku = b.ku === true;
    const price = !ku && Number.isInteger(b.price) && b.price >= 0 ? b.price : null;
    return [{ title, asin: ASIN.test(b.asin) ? b.asin : '', price, ku, skip: b.skip === true }];
  });
}

/** Amazon（Kindle 版）を開く URL。ASIN があれば商品ページ、無ければ Kindle ストアを書名で検索する */
export function amazonKindleUrl({ title, asin } = {}) {
  if (ASIN.test(asin)) return `https://www.amazon.co.jp/dp/${asin}`;
  const q = String(title || '').trim();
  return q ? `https://www.amazon.co.jp/s?k=${encodeURIComponent(q)}&i=digital-text` : '';
}

export function formatPrice(book) {
  if (book.ku) return 'Kindle Unlimited 対象';
  if (book.price === null) return PRICE_REASON_LABELS[book.priceReason] ? `価格情報なし（${PRICE_REASON_LABELS[book.priceReason]}）` : '価格情報なし';
  return `¥${book.price.toLocaleString('ja-JP')}`;
}

/**
 * 書き出す内容。押した単位（タグ+★ / 種別）だけを載せる（押していない項目は取り込み時に DB の値のまま残る）。
 * 保存できないブラウザでは、画面上の状態と公開データの差を書き出す。
 */
export function collectMarks(items, store, { canStore = store.canStore } = {}) {
  const summary = { seen: 0, rated: 0, items: [], unexported: 0 };
  const exported = exportedAt(store);
  const seenAsins = new Set();
  for (const { book, marks } of items) {
    if (!book.asin || seenAsins.has(book.asin)) continue;
    seenAsins.add(book.asin);
    if (marks.tag === 'seen') {
      summary.seen++;
      if (marks.rating) summary.rated++;
    }
    const touched = Object.keys(MARK_GROUPS).filter((group) =>
      MARK_GROUPS[group].some((field) => (canStore ? store.get(KEYS[field] + book.asin) !== null : marks[field] !== book.saved[field])),
    );
    if (!touched.length) continue;
    const item = { asin: book.asin, title: book.title };
    if (touched.includes('tag')) {
      item.tag = marks.tag;
      item.rating = marks.rating ? parseInt(marks.rating, 10) : null;
    }
    if (touched.includes('kind')) item.kind = marks.kind;
    summary.items.push(item);
    if (!canStore || storedAt(store, book.asin) > exported) summary.unexported++;
  }
  return summary;
}

const pad2 = (n) => String(n).padStart(2, '0');

/** 書き出しファイル（kindle-marks v1）。ファイル名は旧画面と同じ kindle-marks-YYYYMMDD-HHMM.json */
export function marksFile(items, now = new Date()) {
  const name = `kindle-marks-${now.getFullYear()}${pad2(now.getMonth() + 1)}${pad2(now.getDate())}-${pad2(now.getHours())}${pad2(now.getMinutes())}.json`;
  return { name, data: { format: 'kindle-marks', version: 1, exported_at: now.toISOString(), items } };
}

/**
 * 書き出しファイル（kindle-marks v1）を確かめ、ブラウザに読み込める項目だけ返す（別の端末への移し替え・バックアップからの戻し）。
 * { exportedAt（書き出した時刻のミリ秒。不明なら 0）, entries: [{ asin, tag?, rating?, kind? }] } を返す。
 * tag を持つ項目は★も一緒に持つ（書き出しと同じ単位）。形式が違えば理由つきで失敗する
 */
export function parseMarksFile(data) {
  if (!data || typeof data !== 'object' || data.format !== 'kindle-marks' || !Array.isArray(data.items)) {
    throw new Error('書き出したタグのファイル（kindle-marks-….json）ではありません');
  }
  if (data.version !== 1) throw new Error(`タグのファイルの版（${data.version}）に対応していません。アプリを更新してください`);
  const entries = [];
  for (const it of data.items) {
    if (!ASIN.test(it?.asin)) continue;
    const entry = { asin: it.asin };
    if (it.tag === '' || it.tag === RETIRED_TAG || isTag(it.tag)) {
      entry.tag = it.tag === RETIRED_TAG ? '' : it.tag;
      entry.rating = it.tag === 'seen' && Number.isInteger(it.rating) && it.rating >= 1 && it.rating <= 5 ? String(it.rating) : '';
    }
    if (isKind(it.kind)) entry.kind = it.kind;
    if ('tag' in entry || 'kind' in entry) entries.push(entry);
  }
  return { exportedAt: Date.parse(data.exported_at) || 0, entries };
}

const sameAsEntry = (marks, entry) => Object.keys(entry).every((k) => k === 'asin' || marks[k] === entry[k]);

/**
 * 読み込んだ項目をブラウザに保存し、画面の状態（items の marks）も合わせる。一覧に無い本は飛ばす（skipped）。
 * ファイルを書き出した後にこの端末で変えた本は上書きしない（kept）。書き出していない変更はこの端末にしか無いため
 */
export function applyImportedMarks(items, store, { exportedAt = 0, entries }, now = Date.now()) {
  const result = { applied: 0, skipped: 0, kept: 0 };
  for (const entry of entries) {
    const targets = items.filter(({ book }) => book.asin === entry.asin);
    if (!targets.length) {
      result.skipped++;
      continue;
    }
    if (storedAt(store, entry.asin) > exportedAt && !sameAsEntry(targets[0].marks, entry)) {
      result.kept++;
      continue;
    }
    for (const item of targets) {
      const marks = { ...item.marks };
      if ('tag' in entry) Object.assign(marks, { tag: entry.tag, rating: entry.rating });
      if ('kind' in entry) marks.kind = entry.kind;
      item.marks = marks;
    }
    for (const group of Object.keys(MARK_GROUPS)) {
      if (MARK_GROUPS[group].some((field) => field in entry)) saveMarks(store, entry.asin, targets[0].marks, group, now);
    }
    result.applied++;
  }
  return result;
}
