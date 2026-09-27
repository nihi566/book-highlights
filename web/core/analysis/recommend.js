// おすすめの本の実在確認（Google Books API。キー不要・CORS 対応なのでブラウザからも使える）
// ローカル LLM は存在しない本をもっともらしく挙げることがあるため、書誌データで照合する。

import { bookKey } from '../text.js';

const ENDPOINT = 'https://www.googleapis.com/books/v1/volumes';

export async function verifyBooks(recs, { fetchImpl, signal } = {}) {
  const doFetch = fetchImpl || globalThis.fetch?.bind(globalThis);
  if (!doFetch) return recs;
  const out = [];
  for (const r of recs) {
    try {
      const v = (await lookup(doFetch, `intitle:${r.title} inauthor:${r.author}`, r, signal)) || (await lookup(doFetch, `intitle:${r.title}`, r, signal));
      out.push({ ...r, verified: v || false });
    } catch {
      // 通信できないときは「未確認」のまま（false にはしない）
      out.push({ ...r });
    }
  }
  return out;
}

async function lookup(doFetch, q, rec, signal) {
  const res = await doFetch(`${ENDPOINT}?q=${encodeURIComponent(q)}&maxResults=5&printType=books`, { signal });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = await res.json();
  return matchVolume(data.items || [], rec);
}

/** 検索結果から書名（と著者）が一致するものを選ぶ */
export function matchVolume(items, rec) {
  const want = bookKey(rec.title);
  const wantAuthor = bookKey(rec.author || '');
  for (const it of items) {
    const info = it.volumeInfo || {};
    const got = bookKey(info.title || '');
    if (!got || !want) continue;
    const titleOk = got === want || (want.length >= 4 && got.includes(want)) || (got.length >= 4 && want.includes(got));
    if (!titleOk) continue;
    const authors = (info.authors || []).join(', ');
    if (wantAuthor && authors) {
      const a = bookKey(authors);
      const authorOk = a.includes(wantAuthor) || wantAuthor.includes(a) || (info.authors || []).some((x) => wantAuthor.includes(bookKey(x)) && bookKey(x).length >= 2);
      if (!authorOk) continue;
    }
    const isbn = (info.industryIdentifiers || []).find((x) => x.type === 'ISBN_13')?.identifier;
    return {
      title: info.title + (info.subtitle ? ` ${info.subtitle}` : ''),
      authors,
      publishedDate: info.publishedDate || '',
      link: info.infoLink || info.canonicalVolumeLink || `https://books.google.com/books?id=${it.id}`,
      thumbnail: (info.imageLinks?.thumbnail || '').replace(/^http:/, 'https:'),
      isbn: isbn || '',
    };
  }
  return null;
}
