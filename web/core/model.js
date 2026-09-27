// ライブラリ（本とハイライト）の正規化モデルとマージ処理
//
// 「点」= 1 つのハイライト。取り込み元（Kindle / Play Books）が違っても同じ形で扱う。
// パーサは ParsedBook[] を返し、mergeParsed() でライブラリへ取り込む。
//
// ParsedBook = { title, author, source, asin?, highlights: ParsedHighlight[] }
// ParsedHighlight = { text, note?, chapter?, location?, locationEnd?, page?, color?, createdAt?, kind? }

import { bookKey, cleanText, hash, normalizeText } from './text.js';

export const SOURCES = {
  kindle: 'Kindle',
  playbooks: 'Play Books',
  manual: '手入力',
};

export const LIBRARY_VERSION = 1;

export function emptyLibrary() {
  return { version: LIBRARY_VERSION, books: {}, highlights: {}, feedback: {}, updatedAt: null };
}

// ---- おすすめの本への反応（読んだ／読みたい／興味なし）。同期され、次のおすすめの選定に使う ----

export const FEEDBACK_LABELS = { read: '読んだ', want: '読みたい', no: '興味なし' };

export function feedbackFor(library, title) {
  const f = library.feedback?.[bookKey(title)];
  return f && f.status ? f : null;
}

/** 反応を付ける。同じ反応をもう一度付けると外す（status: ''） */
export function setFeedback(library, { title, author = '' }, status, now = new Date().toISOString()) {
  if (!Object.hasOwn(FEEDBACK_LABELS, status)) throw new Error(`不明な反応: ${status}`);
  const key = bookKey(title);
  library.feedback = library.feedback || {};
  const cur = library.feedback[key];
  const next = cur?.status === status ? '' : status;
  library.feedback[key] = { key, title: cleanText(title), author: cleanText(author) || cur?.author || '', status: next, updatedAt: now };
  library.updatedAt = now;
  return library.feedback[key];
}

/** 反応ごとの書名の一覧 { read: [...], want: [...], no: [...] } */
export function feedbackByStatus(library) {
  const out = { read: [], want: [], no: [] };
  for (const f of Object.values(library.feedback || {}).sort((a, b) => String(b.updatedAt).localeCompare(String(a.updatedAt)))) if (out[f.status]) out[f.status].push(f);
  return out;
}

export function bookIdFor(title) {
  return 'b' + hash(bookKey(title));
}

export function highlightIdFor(bookId, text) {
  return 'h' + hash(bookId + '|' + normalizeText(text));
}

/** パース結果をライブラリに取り込む。ユーザーの編集（お気に入り・タグ・メモ・削除）は保持する */
export function mergeParsed(library, parsedBooks, { now = new Date().toISOString() } = {}) {
  const stats = { books: 0, booksAdded: 0, added: 0, updated: 0, unchanged: 0, skippedDeleted: 0 };
  for (const pb of parsedBooks) {
    // 書名・著者は 1 行にする（改行入りの書名で Markdown の見出しが崩れないように）
    const title = cleanText(pb.title).replace(/\s+/g, ' ');
    if (!title) continue;
    const bookId = bookIdFor(title);
    let book = library.books[bookId];
    if (!book) {
      book = library.books[bookId] = {
        id: bookId,
        title,
        author: cleanText(pb.author).replace(/\s+/g, ' '),
        sources: [],
        createdAt: now,
        updatedAt: now,
      };
      stats.booksAdded++;
    } else if (book.deleted) {
      // 削除済みの本に再取り込みがあった場合は、本と一緒に消した点ごと復活させる（明示的な取り込み操作のため）
      delete book.deleted;
      book.updatedAt = now;
      book.userUpdatedAt = now;
      for (const h of Object.values(library.highlights)) {
        if (h.bookId === bookId && h.deletedWithBook) {
          delete h.deleted;
          delete h.deletedWithBook;
          h.updatedAt = now;
          h.userUpdatedAt = now;
        }
      }
    }
    stats.books++;
    if (!book.author && pb.author) book.author = cleanText(pb.author).replace(/\s+/g, ' ');
    if (pb.asin && !book.asin) book.asin = pb.asin;
    if (!book.sources.includes(pb.source)) book.sources.push(pb.source);

    const existing = Object.values(library.highlights).filter((h) => h.bookId === bookId);
    const cleaned = pb.highlights.map((h) => ({ ...h, text: cleanText(h.text), note: cleanText(h.note) })).filter((h) => h.text);
    // 伸ばす前の短いハイライトが残るのは Kindle だけ。Play ブックスは現在のハイライトだけが並ぶので、包含関係で消さない
    const extendable = pb.source === 'kindle';
    const incoming = extendable ? dedupeContained(cleaned) : cleaned;
    for (const ph of incoming) {
      const id = highlightIdFor(bookId, ph.text);
      const current = library.highlights[id];
      if (current) {
        if (current.deleted) {
          stats.skippedDeleted++;
          continue;
        }
        if (fillMissing(current, ph, now)) stats.updated++;
        else stats.unchanged++;
        continue;
      }
      // Kindle はハイライトを伸ばすと古い短い版も残るので、包含関係で置き換える
      const norm = normalizeText(ph.text);
      const sameSpot = (h) => extendable && h.source === pb.source && locationsOverlap(h, ph);
      const shorter = existing.find((h) => sameSpot(h) && !h.deleted && h.text.length < ph.text.length && norm.includes(normalizeText(h.text)));
      if (existing.some((h) => sameSpot(h) && h.text.length > ph.text.length && normalizeText(h.text).includes(norm))) {
        stats.unchanged++;
        continue;
      }
      const hl = {
        id,
        bookId,
        source: pb.source,
        kind: ph.kind || 'highlight',
        text: ph.text,
        note: ph.note || '',
        chapter: cleanText(ph.chapter) || '',
        location: numOrNull(ph.location),
        locationEnd: numOrNull(ph.locationEnd),
        page: ph.page != null && ph.page !== '' ? String(ph.page) : '',
        color: ph.color || '',
        createdAt: ph.createdAt || null,
        importedAt: now,
        updatedAt: now,
        favorite: false,
        tags: [],
        userNote: '',
      };
      if (shorter) {
        // ユーザーの編集を引き継いで古い方を置き換える
        hl.favorite = shorter.favorite;
        hl.tags = shorter.tags;
        hl.userNote = shorter.userNote;
        hl.importedAt = shorter.importedAt;
        if (shorter.userUpdatedAt) hl.userUpdatedAt = shorter.userUpdatedAt;
        shorter.deleted = true;
        shorter.supersededBy = id;
        shorter.updatedAt = now;
      }
      library.highlights[id] = hl;
      existing.push(hl);
      stats.added++;
    }
    book.updatedAt = now;
  }
  library.updatedAt = now;
  return stats;
}

function fillMissing(target, src, now) {
  let changed = false;
  for (const key of ['note', 'chapter', 'page', 'color', 'createdAt']) {
    if (!target[key] && src[key]) {
      target[key] = key === 'page' ? String(src[key]) : src[key];
      changed = true;
    }
  }
  for (const key of ['location', 'locationEnd']) {
    if (target[key] == null && numOrNull(src[key]) != null) {
      target[key] = numOrNull(src[key]);
      changed = true;
    }
  }
  if (changed) target.updatedAt = now;
  return changed;
}

function numOrNull(v) {
  if (v === null || v === undefined || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/** 同じ箇所のハイライトか。位置が無ければページで比べ、どちらも無ければ別の箇所とみなす（消しすぎないように） */
function locationsOverlap(a, b) {
  const as = numOrNull(a.location);
  const bs = numOrNull(b.location);
  if (as == null || bs == null) return Boolean(a.page) && String(a.page) === String(b.page ?? '');
  const ae = numOrNull(a.locationEnd) ?? as;
  const be = numOrNull(b.locationEnd) ?? bs;
  return as <= be + 1 && bs <= ae + 1;
}

/** 同じ取り込み内で、他のハイライトに完全に含まれる短いハイライトを除く（Kindle の伸ばしたハイライト対策） */
export function dedupeContained(highlights) {
  const result = [];
  const sorted = highlights.map((h, i) => ({ h, i, n: normalizeText(h.text) })).sort((a, b) => b.n.length - a.n.length);
  const kept = [];
  for (const item of sorted) {
    const dup = kept.find((k) => k.n.includes(item.n) && locationsOverlap(k.h, item.h));
    if (dup) {
      if (!dup.h.note && item.h.note) dup.h = { ...dup.h, note: item.h.note };
      continue;
    }
    kept.push(item);
  }
  kept.sort((a, b) => a.i - b.i);
  for (const k of kept) result.push(k.h);
  return result;
}

// 利用者が編集する欄（★・タグ・自分のメモ・削除）。取り込みで埋まる欄とは別の時刻（userUpdatedAt）で比べる
const USER_FIELDS = ['favorite', 'tags', 'userNote', 'deleted', 'deletedWithBook'];
const BOOK_USER_FIELDS = ['deleted'];

const isBlank = (v) => v === null || v === undefined || v === '' || (Array.isArray(v) && !v.length);
const later = (a, b) => ((a || '') > (b || '') ? a : b) || null;

/** 利用者の編集の時刻。古い版のデータ（userUpdatedAt 無し）は、編集の跡があれば updatedAt で代用する */
function userStamp(item, fields) {
  if (item.userUpdatedAt) return item.userUpdatedAt;
  const edited = fields === BOOK_USER_FIELDS ? item.deleted : item.favorite || item.tags?.length || item.userNote || (item.deleted && !item.supersededBy);
  return edited ? item.updatedAt || '' : '';
}

/** 同じ時刻どうしでも結果が向きに依らないよう、内容で順番を決める */
function order(a, b, stampA, stampB) {
  if (stampA !== stampB) return stampA < stampB ? [a, b] : [b, a];
  return JSON.stringify(a) <= JSON.stringify(b) ? [a, b] : [b, a];
}

function mergeItem(a, b, userFields) {
  // 取り込みで決まる欄: 新しい方を基本に、空欄はもう一方で埋める
  const [older, newer] = order(a, b, a.updatedAt || '', b.updatedAt || '');
  const out = { ...structuredClone(older), ...structuredClone(newer) };
  for (const [k, v] of Object.entries(older)) if (!userFields.includes(k) && isBlank(out[k]) && !isBlank(v)) out[k] = structuredClone(v);
  // 利用者の欄: 編集が新しい方をまとめて採用する
  const ua = userStamp(a, userFields);
  const ub = userStamp(b, userFields);
  const [, winner] = order(a, b, ua, ub);
  for (const k of userFields) {
    if (k in winner) out[k] = structuredClone(winner[k]);
    else delete out[k];
  }
  const stamp = later(a.userUpdatedAt, b.userUpdatedAt);
  if (stamp) out.userUpdatedAt = stamp;
  else delete out.userUpdatedAt;
  out.updatedAt = later(a.updatedAt, b.updatedAt);
  return out;
}

function mergeHighlight(a, b) {
  const out = mergeItem(a, b, USER_FIELDS);
  // 伸ばしたハイライトに置き換わった古い点は、どちらの端末から来ても消えたまま
  const supersededBy = a.supersededBy || b.supersededBy;
  if (supersededBy) Object.assign(out, { deleted: true, supersededBy });
  return out;
}

function mergeBook(a, b) {
  const out = mergeItem(a, b, BOOK_USER_FIELDS);
  out.sources = [...new Set([...(a.sources || []), ...(b.sources || [])])];
  out.createdAt = [a.createdAt, b.createdAt].filter(Boolean).sort()[0] || out.createdAt;
  return out;
}

/**
 * 2 つのライブラリを統合する（PC とスマホの同期用）。項目の欄ごとに統合し、どちら向きに統合しても同じ結果になる。
 * - 取り込みで決まる欄（章・色・位置など）は新しい方を採り、空欄はもう一方で埋める
 * - 利用者の欄（★・タグ・自分のメモ・削除）は、利用者が編集した時刻（userUpdatedAt）が新しい方を採る
 */
export function mergeLibraries(base, incoming) {
  const out = structuredClone(base);
  for (const [kind, merge] of [['books', mergeBook], ['highlights', mergeHighlight]]) {
    out[kind] = out[kind] || {};
    for (const [id, item] of Object.entries(incoming[kind] || {})) {
      const cur = out[kind][id];
      out[kind][id] = cur ? merge(cur, item) : structuredClone(item);
    }
  }
  // おすすめへの反応は、付けた時刻が新しい方
  out.feedback = { ...(base.feedback || {}) };
  for (const [key, f] of Object.entries(incoming.feedback || {})) {
    const cur = out.feedback[key];
    out.feedback[key] = structuredClone(cur ? order(cur, f, cur.updatedAt || '', f.updatedAt || '')[1] : f);
  }
  out.updatedAt = later(base.updatedAt, incoming.updatedAt);
  return out;
}

export function liveHighlights(library) {
  return Object.values(library.highlights).filter((h) => !h.deleted && !library.books[h.bookId]?.deleted);
}

/** 本の中での並び順（位置 → ページ → 日付） */
export function compareInBook(a, b) {
  const la = a.location ?? pageNumber(a.page);
  const lb = b.location ?? pageNumber(b.page);
  if (la != null && lb != null && la !== lb) return la - lb;
  if (la != null && lb == null) return -1;
  if (la == null && lb != null) return 1;
  return String(a.createdAt || a.importedAt || '').localeCompare(String(b.createdAt || b.importedAt || ''));
}

function pageNumber(p) {
  const n = parseInt(p, 10);
  return Number.isFinite(n) ? n : null;
}

export function bookHighlights(library, bookId) {
  return liveHighlights(library).filter((h) => h.bookId === bookId).sort(compareInBook);
}

/** 本の一覧（ハイライト数・最終ハイライト日つき）。新しく線を引いた順 */
export function listBooks(library) {
  const byBook = new Map();
  for (const h of liveHighlights(library)) {
    const e = byBook.get(h.bookId) || { count: 0, last: '' };
    e.count++;
    const t = h.createdAt || h.importedAt || '';
    if (t > e.last) e.last = t;
    byBook.set(h.bookId, e);
  }
  return Object.values(library.books)
    .filter((b) => !b.deleted && byBook.has(b.id))
    .map((b) => ({ ...b, count: byBook.get(b.id).count, lastHighlightedAt: byBook.get(b.id).last }))
    .sort((a, b) => b.lastHighlightedAt.localeCompare(a.lastHighlightedAt) || a.title.localeCompare(b.title, 'ja'));
}

/** 全文検索。空白区切りの AND 検索、タグ（#tag）・ソース・お気に入りで絞り込み */
export function searchHighlights(library, query = '', { source = '', favorite = false, bookId = '' } = {}) {
  const terms = normalizeText(query).split(' ').filter(Boolean);
  return liveHighlights(library)
    .filter((h) => (!source || h.source === source) && (!favorite || h.favorite) && (!bookId || h.bookId === bookId))
    .filter((h) => {
      if (!terms.length) return true;
      const book = library.books[h.bookId];
      const hay = normalizeText([h.text, h.note, h.userNote, h.chapter, book?.title, book?.author, ...(h.tags || []).map((t) => '#' + t)].join(' '));
      return terms.every((t) => hay.includes(t));
    })
    .sort((a, b) => String(b.createdAt || b.importedAt).localeCompare(String(a.createdAt || a.importedAt)));
}

export function updateHighlight(library, id, patch, now = new Date().toISOString()) {
  const h = library.highlights[id];
  if (!h) return null;
  const allowed = ['favorite', 'tags', 'userNote', 'deleted'];
  for (const k of allowed) if (k in patch) h[k] = patch[k];
  if (Array.isArray(h.tags)) h.tags = [...new Set(h.tags.map((t) => String(t).replace(/^#/, '').trim()).filter(Boolean))];
  if (patch.deleted === false) delete h.deletedWithBook;
  h.updatedAt = now;
  h.userUpdatedAt = now;
  library.updatedAt = now;
  return h;
}

export function deleteBook(library, bookId, now = new Date().toISOString()) {
  const b = library.books[bookId];
  if (!b) return;
  b.deleted = true;
  b.updatedAt = now;
  b.userUpdatedAt = now;
  for (const h of Object.values(library.highlights)) {
    if (h.bookId === bookId && !h.deleted) {
      h.deleted = true;
      h.deletedWithBook = true;
      h.updatedAt = now;
      h.userUpdatedAt = now;
    }
  }
  library.updatedAt = now;
}

export function libraryStats(library) {
  const hs = liveHighlights(library);
  const books = listBooks(library);
  const bySource = {};
  for (const h of hs) bySource[h.source] = (bySource[h.source] || 0) + 1;
  return { books: books.length, highlights: hs.length, bySource, favorites: hs.filter((h) => h.favorite).length };
}

/** 日付をシードにした「今日の点」。同じ日には同じ結果になる */
export function dailyPicks(library, count = 3, date = new Date()) {
  const hs = liveHighlights(library).sort((a, b) => a.id.localeCompare(b.id));
  if (!hs.length) return [];
  const day = `${date.getFullYear()}-${date.getMonth() + 1}-${date.getDate()}`;
  const scored = hs.map((h) => ({ h, s: hash(day + h.id) }));
  scored.sort((a, b) => a.s.localeCompare(b.s));
  return scored.slice(0, count).map((x) => x.h);
}
