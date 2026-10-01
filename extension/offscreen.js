// Kindle ノートブック（read.amazon.co.jp/notebook）の HTML を読む係。
// Service Worker には DOMParser が無いので、画面に出ない offscreen ドキュメントで動かす。
// ブラウザのログイン状態（Cookie）をそのまま使うので、Amazon のパスワードや Cookie は保存しない。
// 読み取りのセレクタは web/bookmarklet/kindle-notebook.js と同じ。Amazon の画面が変わったら両方を直す。

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const txt = (el) => (el?.textContent || '').replace(/\s+/g, ' ').trim();
const val = (root, sel) => root.querySelector(sel)?.getAttribute('value') || '';

class NeedLogin extends Error {}

async function get(host, pathAndQuery) {
  const r = await fetch(`https://${host}${pathAndQuery}`, { credentials: 'include', signal: AbortSignal.timeout(30000) });
  if (/\/ap\/(signin|mfa)/.test(new URL(r.url).pathname)) throw new NeedLogin();
  if (!r.ok) throw new Error(`Amazon から HTTP ${r.status} が返りました`);
  return new DOMParser().parseFromString(await r.text(), 'text/html');
}

/** 本の一覧（最近注釈した順） */
async function library(host) {
  const books = [];
  const seen = new Set();
  const collect = (d) =>
    d.querySelectorAll('.kp-notebook-library-each-book').forEach((el) => {
      if (!el.id || seen.has(el.id)) return;
      seen.add(el.id);
      books.push({
        asin: el.id,
        title: txt(el.querySelector('h2.kp-notebook-searchable') || el.querySelector('h2')),
        author: txt(el.querySelector('p.kp-notebook-searchable')).replace(/^[^:：]{1,10}[:：]\s*/, ''),
        lastAnnotated: val(el, '[id^="kp-notebook-annotated-date"]'),
      });
    });
  const first = await get(host, '/notebook');
  collect(first);
  // 本が 1 冊も無く、本棚の枠も無い = ログイン画面など別のページが返っている
  if (!books.length && !first.querySelector('#kp-notebook-library')) throw new NeedLogin();
  let token = val(first, '.kp-notebook-library-next-page-start');
  for (let i = 0; token && i < 100; i++) {
    const d = await get(host, `/notebook?library=list&token=${encodeURIComponent(token)}`);
    collect(d);
    token = val(d, '.kp-notebook-library-next-page-start');
    await sleep(200);
  }
  return books;
}

/** 1 冊分のハイライト */
async function highlights(host, asin) {
  const out = [];
  let next = '';
  let state = '';
  for (let page = 0; page < 200; page++) {
    const d = await get(host, `/notebook?asin=${encodeURIComponent(asin)}&contentLimitState=${encodeURIComponent(state)}&token=${encodeURIComponent(next)}`);
    // ロボット確認の画面などが返ったときに「0 件」として読み終えたことにしない
    if (!d.querySelector('#kp-notebook-annotations, .kp-notebook-annotations-next-page-start, .kp-notebook-content-limit-state')) {
      throw new Error('ハイライトのページを読み取れませんでした');
    }
    d.querySelectorAll('.a-row.a-spacing-base').forEach((row) => {
      const text = txt(row.querySelector('#highlight'));
      const noteEl = row.querySelector('#note');
      noteEl?.querySelectorAll('br').forEach((br) => br.replaceWith('\n'));
      const note = (noteEl?.textContent || '').trim();
      if (!text && !note) return;
      const header = txt(row.querySelector('#annotationHighlightHeader')) || txt(row.querySelector('#annotationNoteHeader'));
      const pageMatch = header.match(/(?:Page|ページ)\s*[:：]?\s*([0-9ivxlcdm]+)/i);
      const color = (row.querySelector('.kp-notebook-highlight')?.className || '').match(/kp-notebook-highlight-(\w+)/);
      out.push({ text, note, location: val(row, '#kp-annotation-location'), page: pageMatch ? pageMatch[1] : '', color: color ? color[1] : '' });
    });
    next = val(d, '.kp-notebook-annotations-next-page-start');
    state = val(d, '.kp-notebook-content-limit-state');
    if (!next) break;
    await sleep(200);
  }
  return out;
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.target !== 'offscreen') return;
  const work = msg.type === 'library' ? library(msg.host).then((books) => ({ books })) : highlights(msg.host, msg.asin).then((list) => ({ highlights: list }));
  work.then(sendResponse, (e) => sendResponse({ error: e instanceof NeedLogin ? '' : e.message, needLogin: e instanceof NeedLogin }));
  return true;
});
