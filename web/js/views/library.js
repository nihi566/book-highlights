// ホーム・本・検索の画面
import { html } from '../html.js';
import { bookHighlights, dailyPicks, libraryStats, listBooks, searchHighlights, SOURCES } from '../../core/model.js';
import { vaultPaths } from '../../core/obsidian.js';
import { formatPrice, searchWishlist } from '../../core/wishlist.js';
import { loadWishlist } from '../wishlist-data.js';
import { bookRow, highlightCard, lineIndex, shelfSwitch, sourceBadge, spineColor } from '../ui.js';

const flow = html`<div class="flow" aria-label="点から立体へ">
  <div class="f-point"><b>点</b>線を引いた一文</div>
  <div class="f-line"><b>線</b>点をつなぐ概念</div>
  <div class="f-plane"><b>面</b>線を束ねたテーマ</div>
  <div class="f-solid"><b>立体</b>知識の全体像</div>
</div>`;

export const home = {
  render({ state, shuffle = 0 }) {
    const lib = state.library;
    const s = libraryStats(lib);
    const a = state.analysis;
    if (!s.highlights) {
      return html`<section class="card hero">
          <h1>本に引いた線を、<br>知識の立体へ。</h1>
          <p class="help">Kindle と Play ブックスのハイライトを 1 か所に集め、Obsidian に写します。PC のローカル LLM が「点」をつないで「線」「面」「立体」に組み立て、次に読む本も提案します。</p>
          ${flow}
          <div class="row">
            <a class="btn primary" href="#/import">ハイライトを取り込む</a>
            <button class="btn" data-action="load-sample">サンプルで試す</button>
          </div>
        </section>
        <div class="section"><h2>使い方</h2></div>
        <ol class="card stack help" style="padding-left:2em">
          <li><b>取り込む</b> — Kindle（端末の My Clippings.txt・アプリのノートブック）と Play ブックス（ドライブのメモ）に対応。</li>
          <li><b>Obsidian に写す</b> — 本ごとのノートにハイライトを書き出します。自分のメモは上書きされません。</li>
          <li><b>AI で立体にする</b> — PC のローカル LLM（Ollama など）が点を線・面・立体に組み立て、おすすめの本を選びます。</li>
        </ol>`;
    }
    const today = new Date(Date.now() + shuffle * 86400000);
    const picks = dailyPicks(lib, 3, today);
    const idx = lineIndex(a);
    const recent = searchHighlights(lib, '').slice(0, 5);
    return html`
      <div class="stats">
        <a class="stat point" href="#/search"><b>${s.highlights}</b><span>点</span></a>
        <a class="stat line" href="#/knowledge"><b>${a ? a.lines.length : '–'}</b><span>線</span></a>
        <a class="stat plane" href="#/knowledge"><b>${a ? a.planes.length : '–'}</b><span>面</span></a>
        <a class="stat solid" href="#/knowledge"><b>${a ? 1 : '–'}</b><span>立体</span></a>
      </div>
      <p class="small muted" style="margin-top:8px">本 ${s.books} 冊 ・ ${Object.entries(s.bySource).map(([k, v]) => `${SOURCES[k] || k} ${v}`).join(' ・ ')} ・ ★ ${s.favorites}</p>

      <div class="section"><h2>今日の点</h2><button class="btn small" data-action="shuffle">別の点</button></div>
      ${picks.map((h) => highlightCard(h, { library: lib, lines: idx.get(h.id) }))}

      ${a
        ? html`<div class="section"><h2>立体</h2><a class="small" href="#/knowledge">知識マップへ</a></div>
            <a class="card solid-card" href="#/knowledge" style="display:block;color:inherit;text-decoration:none">
              <div class="layer-label solid">立体</div>
              <h2>${a.solid.title}</h2>
              <p class="core">${a.solid.core}</p>
            </a>`
        : html`<div class="section"><h2>AI 分析</h2></div>
            <div class="card"><p>点が ${s.highlights} 件たまりました。ローカル LLM で点をつないで、線・面・立体にしてみましょう。</p>
            <a class="btn primary" href="#/knowledge">分析する</a></div>`}

      <div class="section"><h2>最近の点</h2><a class="small" href="#/search">すべて見る</a></div>
      ${recent.map((h) => highlightCard(h, { library: lib, lines: idx.get(h.id) }))}

      <div class="section"><h2>Obsidian</h2></div>
      <div class="card row spread"><span class="help grow">ハイライトと分析結果を Vault に写します。</span><a class="btn" href="#/export">書き出す</a></div>`;
  },
};

export const books = {
  render({ state, query }) {
    const source = query.get('source') || '';
    const sort = query.get('sort') || 'recent';
    const q = (query.get('q') || '').trim().toLowerCase();
    let list = listBooks(state.library).filter((b) => (!source || b.sources.includes(source)) && (!q || `${b.title} ${b.author}`.toLowerCase().includes(q)));
    if (sort === 'title') list = list.sort((a, b) => a.title.localeCompare(b.title, 'ja'));
    if (sort === 'count') list = list.sort((a, b) => b.count - a.count);
    const chip = (key, value, label) => {
      const params = new URLSearchParams(query);
      if (value) params.set(key, value);
      else params.delete(key);
      const on = (query.get(key) || '') === value || (!query.get(key) && key === 'sort' && value === 'recent');
      return html`<a class="chip ${on ? 'on' : ''}" href="#/books?${params}">${label}</a>`;
    };
    return html`<div class="page-head"><div><h1>本</h1><div class="sub">${list.length} 冊</div></div><a class="btn small" href="#/import">＋ 取り込む</a></div>
      ${shelfSwitch('books')}
      <form class="search-box" data-form="book-filter" role="search"><input type="search" name="q" value="${query.get('q') || ''}" placeholder="書名・著者で絞り込む" aria-label="書名・著者で絞り込む"></form>
      <div class="chips">${chip('source', '', 'すべて')}${chip('source', 'kindle', 'Kindle')}${chip('source', 'playbooks', 'Play Books')}</div>
      <div class="chips" style="margin-top:6px">${chip('sort', 'recent', '最近')}${chip('sort', 'title', '書名')}${chip('sort', 'count', '点の数')}</div>
      ${list.length ? html`<ul class="book-list">${list.map(bookRow)}</ul>` : html`<p class="empty">本がありません。<a href="#/import">取り込む</a></p>`}`;
  },
};

export const book = {
  render({ state, params }) {
    const b = state.library.books[params.id];
    if (!b || b.deleted) return html`<p class="empty">本が見つかりません。<a href="#/books">本の一覧へ</a></p>`;
    const hs = bookHighlights(state.library, b.id);
    const idx = lineIndex(state.analysis);
    const { vaultName, root } = state.settings;
    const notePath = vaultPaths(state.library, null, root).books[b.id];
    const obsidianUrl = vaultName && notePath ? `obsidian://open?vault=${encodeURIComponent(vaultName)}&file=${encodeURIComponent(notePath)}` : '';
    const linesHere = (state.analysis?.lines || []).filter((l) => l.bookIds?.includes(b.id));
    let chapter = null;
    const items = [];
    for (const h of hs) {
      if (h.chapter && h.chapter !== chapter) {
        chapter = h.chapter;
        items.push(html`<h3 class="chapter">${chapter}</h3>`);
      }
      items.push(highlightCard(h, { library: state.library, lines: idx.get(h.id), showBook: false }));
    }
    return html`<a class="back" href="#/books">‹ 本</a>
      <div class="page-head">
        <div class="row" style="flex-wrap:nowrap;align-items:flex-start;gap:12px">
          <span class="book-spine" style="background:${spineColor(b.title)}" aria-hidden="true">${[...b.title][0]}</span>
          <div><h1>${b.title}</h1><div class="sub">${b.author || '著者不明'} ${b.sources.map(sourceBadge)} ・ ${hs.length} 点</div></div>
        </div>
      </div>
      <div class="row">
        ${obsidianUrl ? html`<a class="btn small" href="${obsidianUrl}">Obsidian で開く</a>` : html`<a class="btn small" href="#/export">Obsidian に写す</a>`}
        <button class="btn small danger" data-action="delete-book" data-id="${b.id}">この本を削除</button>
      </div>
      ${linesHere.length ? html`<div class="section"><h2>この本から伸びる線</h2></div><div class="hl-lines">${linesHere.map((l) => html`<a class="line-chip" href="#/knowledge/line/${l.id}">${l.name}</a>`)}</div>` : ''}
      <div style="margin-top:16px">${items}</div>`;
  },
};

export const search = {
  render({ state, query }) {
    const q = query.get('q') || '';
    return html`<div class="page-head"><h1>検索</h1></div>
      <form class="search-box" data-form="search" role="search">
        <input type="search" name="q" value="${q}" placeholder="言葉・書名・#タグ（空白で AND）" aria-label="ハイライトを検索" autocomplete="off" ${q ? '' : 'autofocus'}>
      </form>
      <div class="chips" id="search-filters"></div>
      <div id="search-wishlist"></div>
      <div id="search-results"></div>`;
  },
  mount(root, ctx) {
    const input = root.querySelector('input[name="q"]');
    const update = () => renderResults(root, ctx, input.value);
    let t;
    input.addEventListener('input', () => {
      clearTimeout(t);
      t = setTimeout(() => {
        const params = new URLSearchParams(ctx.query);
        if (input.value) params.set('q', input.value);
        else params.delete('q');
        history.replaceState(null, '', `#/search${params.toString() ? '?' + params : ''}`);
        ctx.query = params;
        update();
      }, 150);
    });
    update();
  },
};

function renderResults(root, ctx, q) {
  const { state, query } = ctx;
  const source = query.get('source') || '';
  const fav = query.get('fav') === '1';
  const results = searchHighlights(state.library, q, { source, favorite: fav });
  const idx = lineIndex(state.analysis);
  const link = (patch, label, on) => {
    const p = new URLSearchParams(query);
    for (const [k, v] of Object.entries(patch)) v ? p.set(k, v) : p.delete(k);
    return html`<a class="chip ${on ? 'on' : ''}" href="#/search?${p}">${label}</a>`;
  };
  root.querySelector('#search-filters').innerHTML = String(html`${link({ source: '' }, 'すべて', !source)}${link({ source: 'kindle' }, 'Kindle', source === 'kindle')}${link({ source: 'playbooks' }, 'Play Books', source === 'playbooks')}${link({ fav: fav ? '' : '1' }, '★ お気に入り', fav)}`);
  const shown = results.slice(0, 200);
  root.querySelector('#search-results').innerHTML = String(html`<p class="small muted">${results.length} 件${results.length > shown.length ? `（先頭 ${shown.length} 件を表示）` : ''}</p>
    ${shown.map((h) => highlightCard(h, { library: state.library, lines: idx.get(h.id), query: q }))}
    ${!results.length ? html`<p class="empty">見つかりませんでした</p>` : ''}`);
  renderWishlistHits(root, q);
}

let wishlistToken = 0;

/** 検索語に当たる欲しい本（先頭 5 件）。読めないときは何も出さない（ハイライトの検索を邪魔しない） */
function renderWishlistHits(root, q) {
  const box = root.querySelector('#search-wishlist');
  const token = ++wishlistToken;
  // #タグはハイライト用なので、欲しい本の画面へ渡す語から外す
  const words = String(q || '').split(/\s+/).filter((w) => w && !w.startsWith('#')).join(' ');
  if (!words) {
    box.innerHTML = '';
    return;
  }
  loadWishlist()
    .then((w) => {
      if (token !== wishlistToken || !box.isConnected) return;
      const hits = searchWishlist(w.books, words);
      box.innerHTML = hits.length
        ? String(html`<section class="wishlist-hits"><div class="section"><h2>欲しい本</h2><a class="small" href="#/wishlist?q=${encodeURIComponent(words)}">欲しい本で見る（${hits.length} 件）</a></div>
            <ul class="book-list">${hits.slice(0, 5).map(wishlistHitRow)}</ul></section>`)
        : '';
    })
    .catch(() => {
      if (token === wishlistToken && box.isConnected) box.innerHTML = '';
    });
}

function wishlistHitRow(b) {
  const inner = html`<span class="book-spine" style="background:${spineColor(b.title)}" aria-hidden="true">${[...b.title][0] || ''}</span>
    <span class="grow"><span class="title">${b.title}</span><span class="meta">${formatPrice(b)}</span></span>`;
  return b.asin
    ? html`<li><a class="book-item" href="https://www.amazon.co.jp/dp/${b.asin}" target="_blank" rel="noopener noreferrer" aria-label="${b.title}（Amazon で開く）">${inner}</a></li>`
    : html`<li><a class="book-item" href="#/wishlist?q=${encodeURIComponent(b.title)}">${inner}</a></li>`;
}
