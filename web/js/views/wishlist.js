// 価格チェックの画面（スクレイピングした欲しい本の価格と、その履歴）。データは kindle-wishlist-site が公開する wishlist.json を読むだけで、
// タグ・★・種別はブラウザ（localStorage）に旧画面と同じキーで保存する（core/wishlist.js）。
import { html } from '../html.js';
import { download } from '../services.js';
import { spineColor, toast } from '../ui.js';
import { applyImportedMarks, browserStore, cleanupSyncedMarks, collectMarks, filterWishlist, formatPrice, inShelf, KEYS, loadMarks, marksFile, memoryStore, openWishlistFilters, parseMarksFile, priceChange, priceSparkline, priceTotal, readingCounts, readingLookup, saveMarks, shelfCounts, TAG_FILTER_LABELS, TAG_LABELS, tagCounts, toggleMark } from '../../core/wishlist.js';
import { listBooks } from '../../core/model.js';
import { isoDate } from '../../core/text.js';
import { cachedWishlist, loadWishlist } from '../wishlist-data.js';

const SORTS = { default: '標準（書名）', 'price-asc': '価格が安い順', 'price-desc': '価格が高い順', 'price-drop': '値下がり額が大きい順', 'scraped-desc': 'スクレイピングの最新順', rating: '評価が高い順' };
const SHELVES = { all: 'すべて', wanted: '読みたい', purchased: '購入済み' };
const shelfLabel = (shelf, n) => `${SHELVES[shelf]} ${n}`;
// 購入済みの内訳（本棚の線の有無で分ける）
const READINGS = { all: 'すべて', unread: 'まだ線が無い', reading: '読書中' };
const readingLabel = (reading, n) => `${READINGS[reading]} ${n}`;
const COVER = (asin) => `https://images-na.ssl-images-amazon.com/images/P/${asin}.09.MZZZZZZZ.jpg`;

// localStorage に保存できないブラウザ用。画面を移っても付けたタグが残るよう 1 つだけ持ち、
// canStore: false のままにして「閉じる前に書き出して」の案内と公開データとの差の書き出しを使う（旧画面と同じ）
const fallbackStore = { ...memoryStore(), canStore: false };
let filters = { shelf: 'all', reading: 'all', q: '', sort: 'default', ku: false, min: '', max: '', tag: 'all', kind: 'all' };
let normalFilters = null; // 検索・おすすめ・ホームのリンクから開いている間だけ、開く前の条件（openWishlistFilters）

function lastScrapedText(iso) {
  if (!iso) return '未取得';
  return dateTimeText(iso);
}

// ISO 形式の日時を「YYYY-MM-DD HH:MM」にする（秒以下は出さない）
function dateTimeText(iso) {
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(iso);
  return m ? `${m[1]} ${m[2]}` : iso;
}

export const wishlist = {
  render() {
    return html`<div class="page-head"><div><h1>価格チェック</h1><div class="sub" id="wl-sub">スクレイピングした本の価格</div></div></div>
      <div id="wl-body"><p class="loading">欲しい本を読み込み中…</p></div>`;
  },
  mount(root, ctx) {
    const body = root.querySelector('#wl-body');
    const store = browserStore();
    const marksStore = store.canStore ? store : fallbackStore;
    // 検索の「欲しい本で見る」・おすすめの印から来たときは、その語だけで絞り込んで開く。
    // ホームの「Kindle Unlimited 対象をすべて見る」（ku=1）は KU だけで絞り込んで開く
    //（ほかの条件が残っていると、リンクに出した件数と合わない）。次に普通に開いたときはリンク前の条件に戻す
    const q = ctx?.query?.get('q') || '';
    const ku = ctx?.query?.get('ku') === '1';
    ({ filters, normal: normalFilters } = openWishlistFilters(filters, normalFilters, { q, ku, refresh: Boolean(ctx?.refresh) }));
    const show = (w) => {
      // 購入済みの本を読み始めたか（本棚に線があるか）を引く
      const reading = readingLookup(ctx?.state?.library ? listBooks(ctx.state.library) : []);
      const items = w.books.map((book) => {
        cleanupSyncedMarks(marksStore, book);
        return { book, marks: loadMarks(book, marksStore), reading: reading(book) };
      });
      mountList(root, body, items, marksStore, w.lastScraped);
    };
    // 読み込み済みならすぐ描く（同期のあとの描き直しで一覧が一瞬消え、表示位置が先頭に戻らないように）
    const cached = cachedWishlist();
    if (cached) return show(cached);
    loadWishlist()
      .then((w) => {
        if (body.isConnected) show(w);
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

function mountList(root, body, items, store, lastScraped) {
  const shelfCount = shelfCounts(items);
  // リンクから開いている間は保存済みのタグ・種別の絞り込みを使わず、変えても保存しない（次に普通に開けば保存値に戻る）
  const linked = normalFilters !== null;
  const saveFilter = (key, value) => {
    if (!linked) store.set(key, value);
  };
  if (!linked) {
    filters.tag = storedFilter(store, KEYS.tagFilter, Object.keys(TAG_FILTER_LABELS));
    filters.kind = storedFilter(store, KEYS.kindFilter, ['manga', 'book']);
  }
  root.querySelector('#wl-sub').textContent = `スクレイピングした本の価格 ${items.length} 冊`;
  body.innerHTML = String(html`
    <p class="small muted">価格の最終取得: ${lastScrapedText(lastScraped)}</p>
    <div class="chips" role="group" aria-label="表示する分類">${Object.entries(SHELVES).map(([k, label]) => html`<button type="button" class="chip" data-wl-shelf="${k}" aria-pressed="${String(filters.shelf === k)}">${shelfLabel(k, shelfCount[k])}</button>`)}</div>
    <div class="chips" role="group" aria-label="購入済みの内訳" id="wl-reading" ${filters.shelf === 'purchased' ? '' : 'hidden'}>${Object.keys(READINGS).map((k) => html`<button type="button" class="chip" data-wl-reading="${k}" aria-pressed="${String(filters.reading === k)}"></button>`)}</div>
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
      <div class="row"><button type="button" class="btn small" id="wl-export">読んだ・評価を書き出す</button><button type="button" class="btn small" id="wl-import">書き出したファイルを読み込む</button></div>
      <input type="file" id="wl-import-file" accept=".json,application/json" hidden>
      <p class="small muted">書き出したファイルは、別の端末やブラウザのデータを消した後に「読み込む」で戻せます。</p>
      <p class="small muted" id="wl-export-status" role="status"></p>
    </div>`);

  const $ = (id) => body.querySelector(`#${id}`);
  const list = $('wl-list');

  const renderItems = () => {
    const r = filterWishlist(items, filters);
    // タグの選択肢に、いまの分類（すべて/読みたい/購入済み）で選ぶと残る件数を出す
    const inCurrentShelf = items.filter((item) => inShelf(item, filters.shelf));
    const counts = tagCounts(inCurrentShelf);
    for (const option of $('wl-tag').options) option.textContent = `${TAG_FILTER_LABELS[option.value]}（${counts[option.value]}）`;
    // 「購入済み」タグの付け外しで分類の件数が変わる
    const shelves = shelfCounts(items);
    for (const b of body.querySelectorAll('[data-wl-shelf]')) b.textContent = shelfLabel(b.dataset.wlShelf, shelves[b.dataset.wlShelf]);
    const readings = readingCounts(items);
    for (const b of body.querySelectorAll('[data-wl-reading]')) b.textContent = readingLabel(b.dataset.wlReading, readings[b.dataset.wlReading]);
    $('wl-reading').hidden = filters.shelf !== 'purchased';
    $('wl-price-error').hidden = !r.priceRangeInvalid;
    $('wl-count').textContent = `${r.items.length}件 / 全${inCurrentShelf.length}件を表示${totalText(r.items)}`;
    $('wl-reset').hidden = !((filters.shelf === 'purchased' && filters.reading !== 'all') || filters.q.trim() || filters.ku || filters.min !== '' || filters.max !== '' || filters.tag !== 'all' || filters.kind !== 'all' || filters.sort !== 'default');
    $('wl-empty').hidden = r.items.length !== 0;
    list.innerHTML = String(html`${r.items.map(itemRow)}`);
    const s = collectMarks(items, store);
    $('wl-marks-summary').textContent = `読んだ ${s.seen}件（★評価 ${s.rated}件）・まだ書き出していない変更 ${s.unexported}件${store.canStore ? '' : '（このブラウザには保存できないため、画面を閉じる前に書き出してください）'}`;
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
    if (btn.dataset.wlReading) {
      filters.reading = btn.dataset.wlReading;
      setPressed('data-wl-reading', filters.reading);
      return renderItems();
    }
    if (btn.dataset.wlKindFilter) {
      filters.kind = btn.dataset.wlKindFilter;
      saveFilter(KEYS.kindFilter, filters.kind === 'all' ? null : filters.kind);
      setPressed('data-wl-kind-filter', filters.kind);
      return renderItems();
    }
    if (btn.id === 'wl-reset') {
      Object.assign(filters, { reading: 'all', q: '', sort: 'default', ku: false, min: '', max: '', tag: 'all', kind: 'all' });
      setPressed('data-wl-reading', 'all');
      saveFilter(KEYS.tagFilter, null);
      saveFilter(KEYS.kindFilter, null);
      $('wl-q').value = $('wl-min').value = $('wl-max').value = '';
      $('wl-sort').value = 'default';
      $('wl-tag').value = 'all';
      $('wl-ku').checked = false;
      setPressed('data-wl-kind-filter', 'all');
      return renderItems();
    }
    if (btn.id === 'wl-export') return exportMarks();
    if (btn.id === 'wl-import') return $('wl-import-file').click();
    const li = btn.closest('li[data-asin]');
    if (!li) return;
    const item = items.find(({ book }) => book.asin === li.dataset.asin);
    const press = btn.dataset.tag ? { tag: btn.dataset.tag } : btn.dataset.rating ? { rating: btn.dataset.rating } : null;
    if (!item || !press) return;
    const { marks, group } = toggleMark(item.marks, press);
    item.marks = marks;
    saveMarks(store, item.book.asin, marks, group);
    // 描き直すとフォーカスが外れるので、押したボタンが残っていれば戻す（キーボード操作向け）
    const selector = btn.dataset.tag ? `[data-tag="${btn.dataset.tag}"]` : `[data-rating="${btn.dataset.rating}"]`;
    renderItems();
    list.querySelector(`li[data-asin="${item.book.asin}"] ${selector}`)?.focus();
  });

  const onInput = (id, key, transform = (el) => el.value) => {
    $(id).addEventListener(id === 'wl-ku' || id === 'wl-sort' || id === 'wl-tag' ? 'change' : 'input', (e) => {
      filters[key] = transform(e.target);
      if (key === 'tag') saveFilter(KEYS.tagFilter, filters.tag === 'all' ? null : filters.tag);
      renderItems();
    });
  };
  onInput('wl-q', 'q');
  onInput('wl-sort', 'sort');
  onInput('wl-tag', 'tag');
  onInput('wl-min', 'min');
  onInput('wl-max', 'max');
  onInput('wl-ku', 'ku', (el) => el.checked);
  $('wl-import-file').addEventListener('change', (e) => {
    const file = e.target.files[0];
    e.target.value = ''; // 同じファイルをもう一度選んでも読み込めるように
    if (file) importMarks(file);
  });

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

  async function importMarks(file) {
    const status = $('wl-export-status');
    let parsed;
    try {
      parsed = parseMarksFile(JSON.parse(await file.text()));
    } catch (e) {
      status.textContent = `${file.name} を読み込めませんでした（${e instanceof SyntaxError ? 'JSON の形式ではありません' : e.message}）。「読んだ・評価を書き出す」で保存したファイルを選んでください。`;
      return;
    }
    const r = applyImportedMarks(items, store, parsed);
    const notes = [r.kept && `このファイルより後にこの端末で変えた ${r.kept}件はそのままにしました`, r.skipped && `欲しい本の一覧に無い ${r.skipped}件は飛ばしました`].filter(Boolean);
    status.textContent = `${file.name} から ${r.applied}件のタグ・★・種別を読み込みました${notes.length ? `（${notes.join('。')}）` : ''}。${store.canStore ? '' : 'このブラウザには保存できないため、画面を閉じると元に戻ります。'}`;
    toast('読み込みました');
    renderItems();
  }

  renderItems();
}

// 「まとめて買ったらいくらか」。KU・価格なしの本は数えず、混ざっていればその冊数を添える
function totalText(visible) {
  const t = priceTotal(visible);
  if (!t.priced) return '';
  return `・合計 ¥${t.total.toLocaleString('ja-JP')}${t.unpriced ? `（価格なし ${t.unpriced}冊は除く）` : ''}`;
}

function changeBadges(book) {
  const c = priceChange(book);
  if (!c) return '';
  const d = new Date(c.changedAt ?? NaN); // null を渡すと 1970/1/1 になる
  const when = Number.isNaN(d.getTime()) ? '' : `（${d.getMonth() + 1}/${d.getDate()}）`;
  const amount = `¥${Math.abs(c.diff).toLocaleString('ja-JP')}`;
  const moved = c.diff === 0 ? '' : html` <span class="badge ${c.diff < 0 ? 'down' : 'up'}">${amount} ${c.diff < 0 ? '値下がり' : '値上がり'}${when}</span>`;
  return html`${moved}${c.lowest ? html` <span class="badge down">最安値</span>` : ''}`;
}

// スクレイピングの履歴から作る価格推移の小さなグラフ（価格のある取得が 2 回以上の本だけ）。最初より安ければ緑、高ければ赤
function sparkline(book) {
  const W = 120;
  const H = 28;
  const s = priceSparkline(book.history, { width: W, height: H, pad: 3 });
  if (!s) return '';
  const yen = (n) => `¥${n.toLocaleString('ja-JP')}`;
  const trend = s.last < s.first ? 'down' : s.last > s.first ? 'up' : '';
  const points = s.points.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  const [lx, ly] = s.points[s.points.length - 1];
  return html`<svg class="wl-spark ${trend}" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="価格の推移（${s.count} 回）: 最高 ${yen(s.max)}・最安 ${yen(s.min)}・最後 ${yen(s.last)}">
    <polyline points="${points}" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"></polyline>
    <circle cx="${lx.toFixed(1)}" cy="${ly.toFixed(1)}" r="2.5" fill="currentColor"></circle>
  </svg>`;
}

// スクレイピングの履歴（新しい順）。履歴の無い古いデータでは最終取得日時だけ出す
function historyBlock(book) {
  if (!book.history.length) return book.scrapedAt ? html`<p class="small muted wl-history-none">最終取得: ${dateTimeText(book.scrapedAt)}</p>` : '';
  const rows = [...book.history].sort((a, b) => b.at.localeCompare(a.at));
  const value = (r) => (r.ku ? 'Kindle Unlimited 対象' : r.price === null ? '取得できず' : `¥${r.price.toLocaleString('ja-JP')}`);
  return html`<details class="wl-history">
    <summary>スクレイピングの履歴（${rows.length} 回・最終 ${dateTimeText(rows[0].at)}）</summary>
    <table><thead><tr><th scope="col">取得日時</th><th scope="col">価格</th></tr></thead>
      <tbody>${rows.map((r) => html`<tr><td>${dateTimeText(r.at)}</td><td>${value(r)}</td></tr>`)}</tbody></table>
  </details>`;
}

// 購入済みの本に、読み始めたか（本棚の線の数と最後に線を引いた日）を出す
function readingBadge(item) {
  if (!inShelf(item, 'purchased')) return '';
  const r = item.reading;
  if (!r) return html` <span class="badge">まだ線が無い</span>`;
  return html` <span class="badge">線 ${r.count} 本${r.lastHighlightedAt ? `・最終 ${isoDate(r.lastHighlightedAt)}` : ''}</span>`;
}

function itemRow({ book, marks, reading }) {
  const cover = html`<span class="wl-cover" style="background:${spineColor(book.title)}" aria-hidden="true">${[...book.title][0] || ''}${book.asin ? html`<img src="${COVER(book.asin)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer">` : ''}</span>`;
  const text = html`<span class="grow"><span class="title">${book.title}</span><span class="meta">${formatPrice(book)}${changeBadges(book)}${book.ku ? html` <span class="badge ku">KU</span>` : ''}${inShelf({ book, marks }, 'wanted') ? html` <span class="badge">読みたい</span>` : ''}${inShelf({ book, marks }, 'purchased') ? html` <span class="badge">購入済み</span>` : ''}${readingBadge({ book, marks, reading })}</span>${sparkline(book)}</span>`;
  const value = parseInt(marks.rating, 10) || 0;
  return html`<li class="wl-item" data-asin="${book.asin}">
    ${book.asin ? html`<a class="wl-main" href="https://www.amazon.co.jp/dp/${book.asin}" target="_blank" rel="noopener noreferrer" aria-label="${book.title}（Amazon で開く）">${cover}${text}</a>` : html`<div class="wl-main">${cover}${text}</div>`}
    ${book.asin
      ? html`<div class="wl-marks">
          <span class="chips" role="group" aria-label="タグ">${Object.entries(TAG_LABELS).map(([k, label]) => html`<button type="button" class="chip wl-tag" data-tag="${k}" aria-pressed="${String(marks.tag === k)}">${label}</button>`)}</span>
          ${marks.tag === 'seen' ? html`<span class="wl-stars" role="group" aria-label="★評価（同じ★をもう一度押すと取り消し）">${[1, 2, 3, 4, 5].map((n) => html`<button type="button" class="icon-btn wl-star ${n <= value ? 'on' : ''}" data-rating="${n}" aria-pressed="${String(n === value)}" aria-label="★${n}">${n <= value ? '★' : '☆'}</button>`)}</span>` : ''}
        </div>`
      : ''}
    ${historyBlock(book)}
  </li>`;
}
