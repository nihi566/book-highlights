// 画面の部品（ハイライトのカード、本の行、トースト、シート）
import { html, mark } from './html.js';
import { SOURCES } from '../core/model.js';
import { hash, isoDate } from '../core/text.js';
import { kindleSyncState } from '../core/kindle-status.js';

export const COLOR_VAR = {
  yellow: 'var(--hl-yellow)',
  blue: 'var(--hl-blue)',
  green: 'var(--hl-green)',
  pink: 'var(--hl-pink)',
  orange: 'var(--hl-orange)',
  red: 'var(--hl-red)',
  purple: 'var(--hl-purple)',
};

export const sourceBadge = (s) => html`<span class="badge ${s}">${SOURCES[s] || s}</span>`;

export function spineColor(title) {
  const h = parseInt(hash(title).slice(0, 4), 36) % 360;
  return `hsl(${h} 32% 42%)`;
}

/** ハイライト ID → それを含む線 の対応表 */
export function lineIndex(analysis) {
  const map = new Map();
  for (const l of analysis?.lines || []) {
    for (const id of l.highlightIds) {
      if (!map.has(id)) map.set(id, []);
      map.get(id).push(l);
    }
  }
  return map;
}

export function locationText(h) {
  return [h.location != null ? `位置 ${h.location}` : '', h.page ? `p.${h.page}` : '', isoDate(h.createdAt)].filter(Boolean).join(' · ');
}

export function highlightCard(h, { library, lines = [], query = '', showBook = true } = {}) {
  const book = library.books[h.bookId];
  return html`<article class="hl" style="--hl-color:${COLOR_VAR[h.color] || 'var(--hl-yellow)'}" data-hl="${h.id}">
    <p class="hl-text">${query ? mark(h.text, query) : h.text}</p>
    ${h.note ? html`<div class="hl-note"><b>${h.kind === 'note' ? 'メモ（単独）' : 'メモ'}</b>${query ? mark(h.note, query) : h.note}</div>` : ''}
    ${h.userNote ? html`<div class="hl-note"><b>自分のメモ</b>${h.userNote}</div>` : ''}
    ${h.tags?.length ? html`<div class="hl-tags">${h.tags.map((t) => html`<a href="#/search?q=${encodeURIComponent('#' + t)}">#${t}</a>`)}</div>` : ''}
    ${lines.length ? html`<div class="hl-lines">${lines.map((l) => html`<a class="line-chip" href="#/knowledge/line/${l.id}">${l.name}</a>`)}</div>` : ''}
    <div class="hl-foot">
      <div class="hl-meta">
        ${showBook && book ? html`<a class="book-link" href="#/book/${book.id}">${book.title}</a>` : ''}
        <span>${locationText(h)}</span>
        ${sourceBadge(h.source)}
      </div>
      <div class="hl-actions">
        <button class="icon-btn ${h.favorite ? 'on' : ''}" data-action="fav" data-id="${h.id}" aria-pressed="${String(Boolean(h.favorite))}" aria-label="お気に入り">${h.favorite ? '★' : '☆'}</button>
        <button class="icon-btn" data-action="edit" data-id="${h.id}" aria-label="メモ・タグを編集">✎</button>
        <button class="icon-btn" data-action="copy" data-id="${h.id}" aria-label="引用をコピー">⧉</button>
      </div>
    </div>
  </article>`;
}

/** 本タブの「読んだ本 / 欲しい本」の切り替え（タブバーに増やさず本タブの中で切り替える） */
export function shelfSwitch(active) {
  const item = (key, href, label) =>
    html`<a class="chip ${active === key ? 'on' : ''}" href="${href}" ${active === key ? html`aria-current="page"` : ''}>${label}</a>`;
  return html`<nav class="chips shelf-switch" aria-label="本の種類">${item('books', '#/books', '読んだ本')}${item('wishlist', '#/wishlist', '欲しい本')}</nav>`;
}

export function bookRow(b) {
  return html`<li><a class="book-item" href="#/book/${b.id}">
    <span class="book-spine" style="background:${spineColor(b.title)}" aria-hidden="true">${[...b.title][0]}</span>
    <span class="grow">
      <span class="title">${b.title}</span>
      <span class="meta">${b.author || '著者不明'} ${b.sources.map(sourceBadge)}</span>
    </span>
    <span class="count">${b.count}<span class="unit"> 点</span><small>${isoDate(b.lastHighlightedAt) || '-'}</small></span>
  </a></li>`;
}

let toastTimer;
export function toast(message, ms = 2600) {
  const el = document.getElementById('toast');
  el.textContent = message;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), ms);
}

/** 下から出るシート。content は html``、onSubmit(formData, action) を返す */
export function openSheet(content, onSubmit) {
  const dialog = document.getElementById('sheet');
  dialog.innerHTML = String(html`<form method="dialog">${content}</form>`);
  const form = dialog.querySelector('form');
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const action = e.submitter?.value || 'save';
    if (action === 'cancel') return dialog.close();
    const keep = await onSubmit(new FormData(form), action);
    if (keep !== true) dialog.close();
  });
  dialog.showModal();
  dialog.addEventListener('click', (e) => {
    if (e.target === dialog) dialog.close();
  }, { once: true });
}

const EXPORT_TRIGGERS = { sync: '同期のあと', import: '取り込みのあと', analysis: '分析のあと', manual: '手動', folder: 'このブラウザから' };

/** 時刻を短く（例: 9/27 18:05） */
function timeText(iso) {
  const d = new Date(iso);
  return `${d.getMonth() + 1}/${d.getDate()} ${d.toLocaleTimeString('ja-JP', { hour: '2-digit', minute: '2-digit' })}`;
}

/** 最後に Vault に書き出した結果を 1 行で（例: 9/27 18:05・同期のあと・書き込み 3 件） */
export function lastExportText(last) {
  if (!last?.at) return 'まだ書き出していません';
  const when = timeText(last.at);
  const what = last.error ? `失敗: ${last.error}` : `書き込み ${last.written ?? 0} 件・変更なし ${last.unchanged ?? 0} 件`;
  return `${when}・${EXPORT_TRIGGERS[last.trigger] || last.trigger || ''}・${what}`;
}


/** 経過時間を短く（例: 50 分 / 2 時間 30 分 / 3 日） */
function elapsedText(fromIso, nowIso) {
  const min = Math.max(0, Math.floor((new Date(nowIso) - new Date(fromIso)) / 60000));
  if (min < 60) return `${min} 分`;
  if (min < 60 * 24) return min % 60 ? `${Math.floor(min / 60)} 時間 ${min % 60} 分` : `${Math.floor(min / 60)} 時間`;
  return `${Math.floor(min / 60 / 24)} 日`;
}

/** ブラウザ拡張（Kindle 自動取り込み）の状態を、画面に出す文の並びにする（HTML ではない。出すときはエスケープされる） */
export function kindleSyncLines(ks, now = new Date().toISOString()) {
  const state = kindleSyncState(ks, now);
  if (state === 'none') return ['自動取り込み: まだ拡張から連絡がありません（拡張機能を入れていない場合は、下の手順で設定できます）'];
  const last = ks.lastCheck;
  const checked = `最終確認 ${timeText(last.at)}`;
  const result = { ok: `正常（${checked}）`, login: `Amazon のログインが切れています（${checked}）`, error: `失敗（${checked}）${last.error ? `: ${last.error}` : ''}` };
  const lines = [];
  if (state === 'stale') {
    lines.push(`自動取り込み: 拡張から ${elapsedText(last.at, now)} 連絡がありません。PC のブラウザが閉じているか、PC に送れていない可能性があります`);
    lines.push(`最後の結果: ${result[last.needLogin ? 'login' : last.ok ? 'ok' : 'error']}`);
  } else if (state === 'ok') {
    lines.push(`自動取り込み: ${result.ok}。線がノートブックに反映されるまで数分かかることがあります`);
  } else if (state === 'login') {
    lines.push(`自動取り込み: ${result.login}。PC のブラウザで read.amazon.co.jp/notebook にログインしてください`);
  } else {
    lines.push(`自動取り込み: ${result.error}`);
  }
  lines.push(ks.lastNew?.at ? `最後に新しい点: ${timeText(ks.lastNew.at)}・${ks.lastNew.added} 件` : '最後に新しい点: まだ届いていません');
  return lines;
}

const ALERT_STATES = ['login', 'error', 'stale'];

/** ホームに出す自動取り込みの警告（1 行）。拡張を使っていない・正常なときは空文字 */
export function kindleSyncAlert(ks, now = new Date().toISOString()) {
  return ALERT_STATES.includes(kindleSyncState(ks, now)) ? kindleSyncLines(ks, now)[0] : '';
}

/** ホームの警告欄の中身。PC モードで PC の情報を取れているときだけ出す。押すと取り込み画面で詳しく見られる */
export function kindleAlertBlock(state) {
  const text = state.settings.ai.mode === 'companion' && state.pcInfo ? kindleSyncAlert(state.pcInfo.kindleSync) : '';
  return text ? html`<a class="notice err" href="#/import" style="display:block;margin-bottom:12px;text-decoration:none">${text}（詳しく）</a>` : '';
}
