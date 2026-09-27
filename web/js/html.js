// エスケープ付きのテンプレートリテラル。html`<p>${text}</p>` の埋め込みは自動でエスケープされる。
// 信頼できる HTML を埋め込むときは raw() で包む。配列は連結される。

class Raw {
  constructor(s) {
    this.s = s;
  }
  toString() {
    return this.s;
  }
}

export const raw = (s) => new Raw(String(s ?? ''));

export function esc(v) {
  return String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
}

function part(v) {
  if (v instanceof Raw) return v.s;
  if (Array.isArray(v)) return v.map(part).join('');
  if (v === false || v === null || v === undefined) return '';
  return esc(v);
}

export function html(strings, ...values) {
  let out = strings[0];
  for (let i = 0; i < values.length; i++) out += part(values[i]) + strings[i + 1];
  return new Raw(out);
}

/** 検索語をハイライト表示（エスケープ済み HTML を返す）。元の文字列で照合してから区間ごとにエスケープする */
export function mark(text, query) {
  const src = String(text ?? '');
  const terms = String(query || '')
    .normalize('NFKC')
    .split(/\s+/)
    .filter((t) => t && !t.startsWith('#'));
  if (!terms.length) return raw(esc(src));
  const re = new RegExp(`(${terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`, 'gi');
  return raw(src.split(re).map((part, i) => (i % 2 ? `<mark>${esc(part)}</mark>` : esc(part))).join(''));
}

/** 外部由来の URL は https のものだけ通す（javascript: などを防ぐ） */
export function safeUrl(url) {
  try {
    const u = new URL(String(url || ''));
    return u.protocol === 'https:' ? u.href : '';
  } catch {
    return '';
  }
}
