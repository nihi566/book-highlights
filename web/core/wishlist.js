// 欲しい本（kindle_system が kindle-wishlist-site に公開する wishlist.json）の読み込み・タグ・絞り込み。
// タグ・★・種別は旧画面（kindle-wishlist-site の index.html）と同じ localStorage のキーに保存する。
// 2 つのサイトは同じオリジン（nihi566.github.io）なので、旧画面で付けたタグがそのまま読める。
// 書き出しファイルも旧画面と同じ kindle-marks v1（PC の `python run.py import-marks` で取り込める）。

export const WISHLIST_FORMAT = 'kindle-wishlist';
export const TAG_LABELS = { wanted: '読みたい', unwanted: '読みたくない', purchased: '購入済み', seen: '見た' };
export const KIND_LABELS = { manga: 'マンガ', book: '本' };
export const TAG_FILTER_LABELS = { all: 'すべて', 'hide-unwanted': '「読みたくない」を隠す', wanted: '読みたい', unwanted: '読みたくない', purchased: '購入済み', seen: '見た', untagged: 'タグなし' };

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
// 書き出し・取り込みの単位。★は「見た」に付くのでタグと一緒に扱う
const MARK_GROUPS = { tag: ['tag', 'rating'], kind: ['kind'] };
const ASIN = /^[A-Z0-9]{10}$/;

const isTag = (t) => Object.hasOwn(TAG_LABELS, t);
const isKind = (k) => k === 'manga' || k === 'book';

/** wishlist.json を確かめて、画面で使う形にする。形式が違えば理由つきで失敗する */
export function parseWishlist(data) {
  if (!data || typeof data !== 'object' || data.format !== WISHLIST_FORMAT || !Array.isArray(data.books)) {
    throw new Error('欲しい本のデータ（wishlist.json）の形式ではありません');
  }
  if (data.version !== 1) throw new Error(`欲しい本のデータの版（${data.version}）に対応していません。アプリを更新してください`);
  const books = data.books.map((b, index) => {
    const tag = isTag(b?.tag) ? b.tag : '';
    const rating = tag === 'seen' && Number.isInteger(b.rating) && b.rating >= 1 && b.rating <= 5 ? String(b.rating) : '';
    return {
      asin: ASIN.test(b?.asin) ? b.asin : '',
      title: String(b?.title ?? ''),
      price: Number.isFinite(b?.price) && b.price >= 0 ? b.price : null,
      ku: b?.ku === true,
      wanted: b?.wanted === true,
      purchased: b?.purchased === true,
      saved: { tag, rating, kind: isKind(b?.kind) ? b.kind : 'book' },
      index,
    };
  });
  return { lastScraped: typeof data.last_scraped === 'string' ? data.last_scraped : null, books };
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
    if (tag !== null && (tag === '' || isTag(tag))) marks.tag = tag;
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

const priceValue = (v) => (v === '' || v === undefined || v === null ? NaN : parseFloat(v));

// 評価が高い順: ★の数、「見た」だけで★なしは★の付いた本の後、「見た」以外はさらに後
const ratingValue = (m) => (m.tag !== 'seen' ? 0 : parseInt(m.rating, 10) || 0.5);

function matchesTag(tag, filter) {
  if (filter === 'hide-unwanted') return tag !== 'unwanted';
  if (filter === 'untagged') return tag === '';
  if (isTag(filter)) return tag === filter;
  return true;
}

/**
 * items: [{ book, marks }]。f: { shelf: all|wanted|purchased, q, ku, min, max, tag, kind, sort }
 * 戻り値の priceRangeInvalid は下限 > 上限（そのときは価格帯を無視する）
 */
export function filterWishlist(items, f = {}) {
  const words = String(f.q || '').trim().toLowerCase().split(/[\s　]+/).filter(Boolean);
  let min = priceValue(f.min);
  let max = priceValue(f.max);
  const priceRangeInvalid = !Number.isNaN(min) && !Number.isNaN(max) && min > max;
  if (priceRangeInvalid) min = max = NaN;
  const shelf = f.shelf || 'all';
  const kind = f.kind || 'all';
  const visible = items.filter(({ book, marks }) => {
    if (shelf === 'wanted' && !book.wanted) return false;
    if (shelf === 'purchased' && !book.purchased) return false;
    const hay = `${book.title} ${book.asin}`.toLowerCase();
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

export function formatPrice(book) {
  if (book.ku) return 'Kindle Unlimited 対象';
  if (book.price === null) return '価格情報なし';
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
