// 欲しい本のデータ（kindle_system/ が web/wishlist-site/ に書き出す wishlist.json）の読み込み。欲しい本・検索・知識の画面で共有する。
import { parseWishlist } from '../core/wishlist.js';

// アプリと同じ場所の web/wishlist-site/ を先に読む（GitHub Pages でも bh serve でも同じ相対位置）。
// 手元のファイルが無い・古いクローンのときは、公開 URL から読む（GitHub Pages は CORS を許可している）。
export const WISHLIST_URLS = [
  new URL('../wishlist-site/wishlist.json', import.meta.url).href,
  'https://nihi566.github.io/book-highlights/wishlist-site/wishlist.json',
];

let loaded = null; // 成功した結果だけを覚える（開いている間は読み直さない）
let loading = null;

async function fetchWishlist() {
  let lastError;
  for (const url of [...new Set(WISHLIST_URLS)]) {
    try {
      const res = await fetch(url, { cache: 'no-cache' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return parseWishlist(await res.json());
    } catch (e) {
      lastError = e;
    }
  }
  throw lastError;
}

/** 読み込み済みなら { lastScraped, books }、まだなら null（描き直しで一覧をすぐ出し、表示位置を保つため） */
export const cachedWishlist = () => loaded;

/** { lastScraped, books }。失敗したら reject し、次の呼び出しで読み直す */
export function loadWishlist() {
  if (loaded) return Promise.resolve(loaded);
  loading ??= fetchWishlist()
    .then((w) => (loaded = w))
    .finally(() => (loading = null));
  return loading;
}
