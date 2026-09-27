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
  return { version: LIBRARY_VERSION, books: {}, highlights: {}, updatedAt: null };
}

export function bookIdFor(title) {
  return 'b' + hash(bookKey(title));
}

export function highlightIdFor(bookId, text) {
  return 'h' + hash(bookId + '|' + normalizeText(text));
}

/**
 * パース結果をライブラリに取り込む。ユーザーの編集（お気に入り・タグ・メモ・削除）は保持する
 * reviveDeleted: false … 削除済みの本は復活させずに飛ばす（ブラウザ拡張の自動取り込みなど、人が操作していない取り込み用）
 */
export function mergeParsed(library, parsedBooks, { now = new Date().toISOString(), reviveDeleted = true } = {}) {
  const stats = { books: 0, booksAdded: 0, added: 0, updated: 0, unchanged: 0, skippedDeleted: 0, skippedDeletedBooks: 0 };
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
    } else if (book.deleted && !reviveDeleted) {
      stats.skippedDeletedBooks++;
      continue;
    } else if (book.deleted) {
      // 削除済みの本に再取り込みがあった場合は、本と一緒に消した点ごと復活させる（明示的な取り込み操作のため）
      delete book.deleted;
      book.updatedAt = now;
      for (const h of Object.values(library.highlights)) {
        if (h.bookId === bookId && h.deletedWithBook) {
          delete h.deleted;
          delete h.deletedWithBook;
          h.updatedAt = now;
        }
      }
    }
    stats.books++;
    if (!book.author && pb.author) book.author = cleanText(pb.author).replace(/\s+/g, ' ');
    if (pb.asin && !book.asin) book.asin = pb.asin;
    if (!book.sources.includes(pb.source)) book.sources.push(pb.source);

    const existing = Object.values(library.highlights).filter((h) => h.bookId === bookId);
    const incoming = dedupeContained(pb.highlights.map((h) => ({ ...h, text: cleanText(h.text), note: cleanText(h.note) })).filter((h) => h.text));
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
      const shorter = existing.find((h) => h.source === pb.source && !h.deleted && h.text.length < ph.text.length && norm.includes(normalizeText(h.text)) && locationsOverlap(h, ph));
      if (existing.some((h) => h.source === pb.source && h.text.length > ph.text.length && normalizeText(h.text).includes(norm) && locationsOverlap(h, ph))) {
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

function locationsOverlap(a, b) {
  const as = numOrNull(a.location);
  const bs = numOrNull(b.location);
  if (as == null || bs == null) return true;
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

/** 2 つのライブラリを統合する（PC とスマホの同期用）。項目ごとに updatedAt が新しい方を採用 */
export function mergeLibraries(base, incoming) {
  const out = structuredClone(base);
  for (const kind of ['books', 'highlights']) {
    for (const [id, item] of Object.entries(incoming[kind] || {})) {
      const cur = out[kind][id];
      if (!cur || (item.updatedAt || '') > (cur.updatedAt || '')) {
        out[kind][id] = structuredClone(item);
        if (cur && kind === 'books') out[kind][id].sources = [...new Set([...(cur.sources || []), ...(item.sources || [])])];
      }
    }
  }
  out.updatedAt = [base.updatedAt, incoming.updatedAt].filter(Boolean).sort().pop() || null;
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
  h.updatedAt = now;
  library.updatedAt = now;
  return h;
}

export function deleteBook(library, bookId, now = new Date().toISOString()) {
  const b = library.books[bookId];
  if (!b) return;
  b.deleted = true;
  b.updatedAt = now;
  for (const h of Object.values(library.highlights)) {
    if (h.bookId === bookId && !h.deleted) {
      h.deleted = true;
      h.deletedWithBook = true;
      h.updatedAt = now;
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
