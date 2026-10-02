// ブラウザ拡張の中で DOM にも chrome.* にも依存しない部分（Node のテストからも読み込む）

export const DEFAULTS = {
  companionUrl: 'http://localhost:8787',
  token: '',
  amazonHost: 'read.amazon.co.jp',
  intervalMin: 15,
};

export const AMAZON_HOSTS = ['read.amazon.co.jp', 'read.amazon.com'];

/**
 * 前回の取り込みから変化した本だけを選ぶ。
 * ノートブックの「最後に注釈した日」は日単位なので、同じ日に 2 回目の線を引いても変わらない。
 * そこで「今いちばん新しい日」（一覧は最近注釈した順）と「前回の取り込み時にいちばん新しかった日」と
 * 同じ日付の本は、日付が変わっていなくても毎回読み直す（日をまたいだ直後の取りこぼしも防ぐ）。
 */
export function pickBooksToFetch(books, known, { lastTopDate = '' } = {}) {
  const recent = new Set([books[0]?.lastAnnotated, lastTopDate].filter(Boolean));
  return books.filter((b) => known[b.asin] !== b.lastAnnotated || recent.has(b.lastAnnotated));
}

/** bh serve の /api/import に送る本文（ブックマークレットと同じ JSON 形式を 1 ファイルとして送る） */
export function importBody(books, now = new Date()) {
  const data = {
    format: 'book-highlights/kindle-notebook',
    version: 1,
    exportedAt: now.toISOString(),
    source: 'extension',
    books: books.filter((b) => b.highlights?.length),
  };
  return { files: [{ name: `kindle-auto-${data.exportedAt.slice(0, 10)}.json`, base64: toBase64Utf8(JSON.stringify(data)) }], auto: true };
}

export function toBase64Utf8(text) {
  const bytes = new TextEncoder().encode(text);
  let bin = '';
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

/** 拡張の host_permissions で届く URL か（それ以外は Chrome に遮断される） */
export function isReachableCompanionUrl(url) {
  try {
    const u = new URL(url);
    if (u.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(u.hostname)) return true;
    return u.protocol === 'https:' && /\.ts\.net$/i.test(u.hostname);
  } catch {
    return false;
  }
}

/** PC の /api/kindle-status に送る本文（確認の結果だけ。トークン・URL・本の一覧は含めない） */
export function statusReport(status, settings) {
  return {
    ok: Boolean(status.ok),
    needLogin: Boolean(status.needLogin),
    added: Number.isInteger(status.added) && status.added >= 0 ? status.added : 0,
    intervalMin: Number(settings.intervalMin) || DEFAULTS.intervalMin,
    error: String(status.error || '').slice(0, 300),
  };
}

export function chunk(list, size) {
  const out = [];
  for (let i = 0; i < list.length; i += size) out.push(list.slice(i, i + size));
  return out;
}
