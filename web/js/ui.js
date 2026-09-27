// 画面の部品（ハイライトのカード、本の行、トースト、シート）
import { html, mark } from './html.js';
import { SOURCES } from '../core/model.js';
import { hash, isoDate } from '../core/text.js';

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
