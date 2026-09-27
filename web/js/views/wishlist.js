// 欲しい本の画面（本タブの「欲しい本」）。データは kindle-wishlist-site が公開する wishlist.json を読むだけで、
// タグ・★・種別はブラウザ（localStorage）に旧画面と同じキーで保存する（core/wishlist.js）。
import { html } from '../html.js';
import { download } from '../services.js';
import { shelfSwitch, spineColor, toast } from '../ui.js';
import { browserStore, cleanupSyncedMarks, collectMarks, filterWishlist, formatPrice, KEYS, KIND_LABELS, loadMarks, marksFile, memoryStore, saveMarks, TAG_FILTER_LABELS, TAG_LABELS, toggleMark } from '../../core/wishlist.js';
import { loadWishlist } from '../wishlist-data.js';

const SORTS = { default: '標準（書名）', 'price-asc': '価格が安い順', 'price-desc': '価格が高い順', rating: '評価が高い順' };
const SHELVES = { all: 'すべて', wanted: '読みたい', purchased: '購入済み' };
const COVER = (asin) => `https://images-na.ssl-images-amazon.com/images/P/${asin}.09.MZZZZZZZ.jpg`;

// localStorage に保存できないブラウザ用。画面を移っても付けたタグが残るよう 1 つだけ持ち、
// canStore: false のままにして「閉じる前に書き出して」の案内と公開データとの差の書き出しを使う（旧画面と同じ）
const fallbackStore = { ...memoryStore(), canStore: false };
const filters = { shelf: 'all', q: '', sort: 'default', ku: false, min: '', max: '' };
let linkFilter = false; // いまの filters.q が検索・おすすめのリンクから入ったものか

function lastScrapedText(iso) {
  if (!iso) return '未取得';
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(iso);
  return m ? `${m[1]} ${m[2]}` : iso;
}

export const wishlist = {
  render() {
    return html`<div class="page-head"><div><h1>本</h1><div class="sub" id="wl-sub">欲しい本</div></div></div>
      ${shelfSwitch('wishlist')}
      <div id="wl-body"><p class="loading">欲しい本を読み込み中…</p></div>`;
  },
  mount(root, ctx) {
    const body = root.querySelector('#wl-body');
    const store = browserStore();
    const marksStore = store.canStore ? store : fallbackStore;
    // 検索の「欲しい本で見る」・おすすめの印から来たときは、その語だけで絞り込んで開く
    //（ほかの条件が残っていると、印の付いた本が 0 件になる）。次に普通に開いたときはその語を外す
    const q = ctx?.query?.get('q');
    if (q) {
      Object.assign(filters, { q, shelf: 'all', sort: 'default', ku: false, min: '', max: '' });
      linkFilter = true;
    } else if (linkFilter) {
      filters.q = '';
      linkFilter = false;
    }
    loadWishlist()
      .then((w) => {
        if (!body.isConnected) return;
        const items = w.books.map((book) => {
          cleanupSyncedMarks(marksStore, book);
          return { book, marks: loadMarks(book, marksStore) };
        });
        mountList(root, body, items, marksStore, w.lastScraped, { fromLink: Boolean(q) });
      })
      .catch((e) => {
        if (!body.isConnected) return;
        body.innerHTML = String(html`<p class="notice err">欲しい本のデータを読み込めませんでした（${e.message}）。通信状況を確かめて、もう一度読み込んでください。</p>
          <div class="row" style="margin-top:12px"><button class="btn" type="button" data-wl="retry">もう一度読み込む</button></div>`);
        body.querySelector('[data-wl="retry"]').addEventListener('click', () => {
          // ボタンを消してから読み直す（連打で一覧の処理が二重に付かないように）
          body.innerHTML = '<p class="loading">欲しい本を読み込み中…</p>';
          wishlist.mount(root, ctx);
        });
      });
  },
};

function storedFilter(store, key, allowed) {
  const v = store.get(key);
  return allowed.includes(v) ? v : 'all';
}

function mountList(root, body, items, store, lastScraped, { fromLink = false } = {}) {
  const count = (shelf) => items.filter(({ book }) => shelf === 'all' || book[shelf]).length;
  // リンクから来たときは保存済みのタグ・種別の絞り込みを使わない（保存値は消さないので、次に普通に開けば戻る）
  filters.tag = fromLink ? 'all' : storedFilter(store, KEYS.tagFilter, Object.keys(TAG_FILTER_LABELS));
  filters.kind = fromLink ? 'all' : storedFilter(store, KEYS.kindFilter, ['manga', 'book']);
  root.querySelector('#wl-sub').textContent = `欲しい本 ${items.length} 冊`;
  body.innerHTML = String(html`
    <p class="small muted">価格の最終取得: ${lastScrapedText(lastScraped)}</p>
    <div class="chips" role="group" aria-label="表示する分類">${Object.entries(SHELVES).map(([k, label]) => html`<button type="button" class="chip" data-wl-shelf="${k}" aria-pressed="${String(filters.shelf === k)}">${label} ${count(k)}</button>`)}</div>
    <div class="search-box wl-search" role="search"><input type="search" id="wl-q" value="${filters.q}" placeholder="書名・ASIN で絞り込む（空白で AND）" aria-label="書名・ASIN で絞り込む" autocomplete="off"></div>
    <div class="wl-controls">
      <label class="wl-field"><span>並べ替え</span><select id="wl-sort">${Object.entries(SORTS).map(([k, label]) => html`<option value="${k}" ${filters.sort === k ? 'selected' : ''}>${label}</option>`)}</select></label>
      <label class="wl-field"><span>タグ</span><select id="wl-tag">${Object.entries(TAG_FILTER_LABELS).map(([k, label]) => html`<option value="${k}" ${filters.tag === k ? 'selected' : ''}>${label}</option>`)}</select></label>
      <label class="wl-field wl-price"><span>価格（円）</span><span class="row"><input type="number" id="wl-min" inputmode="numeric" min="0" value="${filters.min}" placeholder="下限" aria-label="価格の下限（円）"><span aria-hidden="true">〜</span><input type="number" id="wl-max" inputmode="numeric" min="0" value="${filters.max}" placeholder="上限" aria-label="価格の上限（円）"></span></label>
      <label class="wl-check"><input type="checkbox" id="wl-ku" ${filters.ku ? 'checked' : ''}> Kindle Unlimited のみ</label>
    </div>
    <div class="chips" role="group" aria-label="種別">${[['all', 'すべて'], ['manga', 'マンガ'], ['book', '本']].map(([k, label]) => html`<button type="button" class="chip" data-wl-kind-filter="${k}" aria-pressed="${String(filters.kind === k)}">${label}</button>`)}</div>
    <p class="notice err wl-price-error" id="wl-price-error" role="alert" hidden>価格の下限が上限より大きいため、価格の条件は使っていません。</p>
    <div class="row spread wl-count-row"><p class="small muted" id="wl-count" aria-live="polite"></p><button type="button" class="btn small" id="wl-reset" hidden>条件をクリア</button></div>
    <ul class="wl-list" id="wl-list"></ul>
    <p class="empty" id="wl-empty" hidden>条件に一致する本がありません。検索語・種別・タグ・価格の条件を見直してください。</p>
    <div class="card wl-export">
      <p class="small" id="wl-marks-summary"></p>
      <div class="row"><button type="button" class="btn small" id="wl-export">見た・評価を書き出す</button></div>
      <p class="small muted" id="wl-export-status" role="status"></p>
    </div>`);

  const $ = (id) => body.querySelector(`#${id}`);
  const list = $('wl-list');

  const renderItems = () => {
    const r = filterWishlist(items, filters);
    $('wl-price-error').hidden = !r.priceRangeInvalid;
    $('wl-count').textContent = `${r.items.length}件 / 全${items.filter(({ book }) => filters.shelf === 'all' || book[filters.shelf]).length}件を表示`;
    $('wl-reset').hidden = !(filters.q.trim() || filters.ku || filters.min !== '' || filters.max !== '' || filters.tag !== 'all' || filters.kind !== 'all' || filters.sort !== 'default');
    $('wl-empty').hidden = r.items.length !== 0;
    list.innerHTML = String(html`${r.items.map(itemRow)}`);
    const s = collectMarks(items, store);
    $('wl-marks-summary').textContent = `見た ${s.seen}件（★評価 ${s.rated}件）・まだ書き出していない変更 ${s.unexported}件${store.canStore ? '' : '（このブラウザには保存できないため、画面を閉じる前に書き出してください）'}`;
  };

  const setPressed = (attr, value) => {
    for (const b of body.querySelectorAll(`[${attr}]`)) b.setAttribute('aria-pressed', String(b.getAttribute(attr) === value));
  };

  body.addEventListener('click', (e) => {
    const btn = e.target.closest('button');
    if (!btn) return;
    if (btn.dataset.wlShelf) {
      filters.shelf = btn.dataset.wlShelf;
      setPressed('data-wl-shelf', filters.shelf);
      return renderItems();
    }
    if (btn.dataset.wlKindFilter) {
      filters.kind = btn.dataset.wlKindFilter;
      store.set(KEYS.kindFilter, filters.kind === 'all' ? null : filters.kind);
      setPressed('data-wl-kind-filter', filters.kind);
      return renderItems();
    }
    if (btn.id === 'wl-reset') {
      Object.assign(filters, { q: '', sort: 'default', ku: false, min: '', max: '', tag: 'all', kind: 'all' });
      store.set(KEYS.tagFilter, null);
      store.set(KEYS.kindFilter, null);
      $('wl-q').value = $('wl-min').value = $('wl-max').value = '';
      $('wl-sort').value = 'default';
      $('wl-tag').value = 'all';
      $('wl-ku').checked = false;
      setPressed('data-wl-kind-filter', 'all');
      return renderItems();
    }
    if (btn.id === 'wl-export') return exportMarks();
    const li = btn.closest('li[data-asin]');
    if (!li) return;
    const item = items.find(({ book }) => book.asin === li.dataset.asin);
    const press = btn.dataset.tag ? { tag: btn.dataset.tag } : btn.dataset.rating ? { rating: btn.dataset.rating } : btn.hasAttribute('data-kind') ? { kind: true } : null;
    if (!item || !press) return;
    const { marks, group } = toggleMark(item.marks, press);
    item.marks = marks;
    saveMarks(store, item.book.asin, marks, group);
    // 描き直すとフォーカスが外れるので、押したボタンが残っていれば戻す（キーボード操作向け）
    const selector = btn.dataset.tag ? `[data-tag="${btn.dataset.tag}"]` : btn.dataset.rating ? `[data-rating="${btn.dataset.rating}"]` : '[data-kind]';
    renderItems();
    list.querySelector(`li[data-asin="${item.book.asin}"] ${selector}`)?.focus();
  });

  const onInput = (id, key, transform = (el) => el.value) => {
    $(id).addEventListener(id === 'wl-ku' || id === 'wl-sort' || id === 'wl-tag' ? 'change' : 'input', (e) => {
      filters[key] = transform(e.target);
      if (key === 'tag') store.set(KEYS.tagFilter, filters.tag === 'all' ? null : filters.tag);
      renderItems();
    });
  };
  onInput('wl-q', 'q');
  onInput('wl-sort', 'sort');
  onInput('wl-tag', 'tag');
  onInput('wl-min', 'min');
  onInput('wl-max', 'max');
  onInput('wl-ku', 'ku', (el) => el.checked);

  // 表紙が無い ASIN では 1x1 の透明画像が返るので、読み込めても外して背表紙の色を見せる
  const dropCover = (e) => {
    if (e.target.tagName === 'IMG' && (e.type === 'error' || e.target.naturalWidth <= 1)) e.target.remove();
  };
  list.addEventListener('load', dropCover, true);
  list.addEventListener('error', dropCover, true);

  function exportMarks() {
    const s = collectMarks(items, store);
    const status = $('wl-export-status');
    if (!s.items.length) {
      status.textContent = 'ブラウザに保存した変更はありません（表示中のタグ・★・種別は公開データと同じです）。';
      return;
    }
    const f = marksFile(s.items);
    download(f.name, `${JSON.stringify(f.data, null, 2)}\n`, 'application/json');
    store.set(KEYS.exportedAt, String(Date.now()));
    status.textContent = `${f.name} を書き出しました（${s.items.length}件）。PC で「python run.py import-marks ファイルのパス」を実行して取り込むと、「python run.py recommend」でローカル LLM のおすすめを出せます。`;
    toast('書き出しました');
    renderItems();
  }

  renderItems();
}

function itemRow({ book, marks }) {
  const other = marks.kind === 'manga' ? 'book' : 'manga';
  const cover = html`<span class="wl-cover" style="background:${spineColor(book.title)}" aria-hidden="true">${[...book.title][0] || ''}${book.asin ? html`<img src="${COVER(book.asin)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer">` : ''}</span>`;
  const text = html`<span class="grow"><span class="title">${book.title}</span><span class="meta">${formatPrice(book)}${book.ku ? html` <span class="badge ku">KU</span>` : ''}${book.wanted ? html` <span class="badge">読みたい</span>` : ''}${book.purchased ? html` <span class="badge">購入済み</span>` : ''}</span></span>`;
  const value = parseInt(marks.rating, 10) || 0;
  return html`<li class="wl-item" data-asin="${book.asin}">
    ${book.asin ? html`<a class="wl-main" href="https://www.amazon.co.jp/dp/${book.asin}" target="_blank" rel="noopener noreferrer" aria-label="${book.title}（Amazon で開く）">${cover}${text}</a>` : html`<div class="wl-main">${cover}${text}</div>`}
    ${book.asin
      ? html`<div class="wl-marks">
          <button type="button" class="chip wl-kind" data-kind="${marks.kind}" aria-label="種別: ${KIND_LABELS[marks.kind]}（押すと${KIND_LABELS[other]}に切り替え）">${KIND_LABELS[marks.kind]}</button>
          <span class="chips" role="group" aria-label="タグ">${Object.entries(TAG_LABELS).map(([k, label]) => html`<button type="button" class="chip wl-tag" data-tag="${k}" aria-pressed="${String(marks.tag === k)}">${label}</button>`)}</span>
          ${marks.tag === 'seen' ? html`<span class="wl-stars" role="group" aria-label="★評価（同じ★をもう一度押すと取り消し）">${[1, 2, 3, 4, 5].map((n) => html`<button type="button" class="icon-btn wl-star ${n <= value ? 'on' : ''}" data-rating="${n}" aria-pressed="${String(n === value)}" aria-label="★${n}">${n <= value ? '★' : '☆'}</button>`)}</span>` : ''}
        </div>`
      : ''}
  </li>`;
}
