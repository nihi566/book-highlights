// 点と線 — Web アプリ本体（ルーティング・操作）
import { html } from './html.js';
import { kv, requestPersistence } from './db.js';
import { loadCache, loadState, save, saveCache, state } from './state.js';
import { buildBookmarklet, companion, detectServedByCompanion, download, fsSupported, pickVault, savedVault, syncWithPc, writeVaultFs } from './services.js';
import { openSheet, toast } from './ui.js';
import { book, books, home, search } from './views/library.js';
import { isolatedView, knowledge, lineView, planeView } from './views/knowledge.js';
import { exportView, importView, settingsView } from './views/settings.js';
import { wishlist } from './views/wishlist.js';
import { FEEDBACK_LABELS, deleteBook, emptyLibrary, mergeParsed, setFeedback, updateHighlight } from '../core/model.js';
import { parseFiles } from '../core/parsers/index.js';
import { applyImport, makeBackup } from '../core/importing.js';
import { isNotebookJson, parseNotebookJson } from '../core/parsers/kindle-notebook.js';
import { renderVault } from '../core/obsidian.js';
import { createZip } from '../core/zip.js';
import { createLlmClient } from '../core/analysis/llm.js';
import { analyzeLibrary, recommendBooks, recommendationNote } from '../core/analysis/pipeline.js';
import { followJob } from '../core/jobs.js';
import { SAMPLE_BOOKS } from '../core/sample.js';
import { safeFileName } from '../core/text.js';

const ROUTES = [
  [/^\/$/, home, 'home'],
  [/^\/books$/, books, 'books'],
  [/^\/book\/(?<id>[\w-]+)$/, book, 'books'],
  [/^\/wishlist$/, wishlist, 'books'],
  [/^\/search$/, search, 'search'],
  [/^\/knowledge$/, knowledge, 'knowledge'],
  [/^\/knowledge\/line\/(?<id>[\w-]+)$/, lineView, 'knowledge'],
  [/^\/knowledge\/plane\/(?<id>[\w-]+)$/, planeView, 'knowledge'],
  [/^\/knowledge\/isolated$/, isolatedView, 'knowledge'],
  [/^\/import$/, importView, 'settings'],
  [/^\/export$/, exportView, 'settings'],
  [/^\/settings$/, settingsView, 'settings'],
];

const view = document.getElementById('view');
let shuffle = 0;
let currentPath = null;

function parseHash() {
  const raw = location.hash.replace(/^#/, '') || '/';
  const [path, qs] = raw.split('?');
  return { path, query: new URLSearchParams(qs || '') };
}

function render({ keepScroll = false } = {}) {
  const { path, query } = parseHash();
  let match = null;
  for (const [re, v, tab] of ROUTES) {
    const m = path.match(re);
    if (m) {
      match = { view: v, tab, params: m.groups || {} };
      break;
    }
  }
  if (!match) match = { view: home, tab: 'home', params: {} };
  const ctx = { state, params: match.params, query, shuffle };
  const y = window.scrollY;
  view.innerHTML = String(match.view.render(ctx));
  match.view.mount?.(view, ctx);
  mountCommon();
  for (const a of document.querySelectorAll('.tabbar a')) {
    if (a.dataset.tab === match.tab) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  }
  // 書き出し・設定の画面では PC の出力先と最後に書き出した時刻を取り直す（10 秒に 1 回まで）
  if ((path === '/export' || path === '/settings') && state.settings.ai.mode === 'companion' && Date.now() - (state.pcInfoAt || 0) > 10000) {
    state.pcInfoAt = Date.now();
    refreshPcInfo();
  }
  if (keepScroll || path === currentPath) window.scrollTo(0, y);
  else window.scrollTo(0, 0);
  currentPath = path;
  updateStatus();
}

function updateStatus() {
  const el = document.getElementById('status');
  const job = state.job;
  el.classList.toggle('busy', Boolean(job?.running));
  el.textContent = job?.running ? job.message || '分析中…' : '';
}

/** 画面ごとの後処理（ドロップゾーン、ブックマークレット） */
function mountCommon() {
  const drop = view.querySelector('#drop');
  if (drop) {
    const input = drop.querySelector('input');
    input.addEventListener('change', () => importFiles([...input.files]));
    drop.addEventListener('dragover', (e) => {
      e.preventDefault();
      drop.classList.add('over');
    });
    drop.addEventListener('dragleave', () => drop.classList.remove('over'));
    drop.addEventListener('drop', (e) => {
      e.preventDefault();
      drop.classList.remove('over');
      importFiles([...e.dataTransfer.files]);
    });
  }
  const bm = view.querySelector('#bookmarklet');
  if (bm) buildBookmarklet().then((href) => (bm.href = href)).catch(() => {});
}

async function persistLibrary() {
  await save.library();
}

// ---- 取り込み ----

async function importFiles(files) {
  if (!files.length) return;
  const out = view.querySelector('#import-result');
  if (out) out.innerHTML = '<p class="loading">読み込み中…</p>';
  try {
    const inputs = await Promise.all(files.map(async (f) => ({ name: f.name, bytes: new Uint8Array(await f.arrayBuffer()) })));
    const parsed = await parseFiles(inputs);
    const { results } = parsed;
    const r = applyImport({ library: state.library, analysis: state.analysis }, parsed);
    const { stats } = r;
    state.library = r.library;
    await persistLibrary();
    if (r.analysisChanged) {
      state.analysis = r.analysis;
      await save.analysis();
    }
    const summary = `新しい点 ${stats.added} 件${stats.updated ? `・更新 ${stats.updated} 件` : ''}${stats.unchanged ? `・既存 ${stats.unchanged} 件` : ''}`;
    toast(`取り込みました: ${summary}`);
    if (out) {
      out.innerHTML = String(html`<div class="card" style="margin-top:12px">
        <p class="notice ${stats.added || stats.backups ? 'ok' : ''}">${summary}${r.analysisChanged ? '（バックアップの新しい分析結果も反映）' : ''}</p>
        <ul class="result-list">${results.map((r) => html`<li>${r.error ? '✗' : '✓'} <b>${r.name}</b><br><span class="small muted">${r.error || `${r.formatLabel} — 本 ${r.books} 冊 / 点 ${r.highlights} 件`}</span></li>`)}</ul>
        <div class="row" style="margin-top:8px"><a class="btn small" href="#/books">本を見る</a><a class="btn small" href="#/export">Obsidian に写す</a></div>
      </div>`);
    }
    autoSyncAfterChange();
  } catch (e) {
    if (out) out.innerHTML = String(html`<p class="notice err">${e.message}</p>`);
  }
}

// Kindle ノートブックのあるドメインだけ（read.amazon.evil.com などは通さない）
const AMAZON_ORIGIN = /^https:\/\/read\.amazon\.(com|co\.jp|co\.uk|de|fr|it|es|nl|ca|in|com\.au|com\.br|com\.mx)$/;

/** ブックマークレットから postMessage でデータを受け取る */
function listenBookmarklet() {
  if (!location.hash.includes('from=bookmarklet') || !window.opener) return;
  window.addEventListener('message', async (e) => {
    if (!AMAZON_ORIGIN.test(e.origin) || e.data?.type !== 'bh-import' || !isNotebookJson(e.data.data)) return;
    const stats = mergeParsed(state.library, parseNotebookJson(e.data.data));
    await persistLibrary();
    toast(`Kindle から取り込みました: 新しい点 ${stats.added} 件`, 5000);
    location.hash = '#/books';
    autoSyncAfterChange();
  });
  window.opener.postMessage({ type: 'bh-ready' }, '*');
}

// ---- AI 分析 ----

function setJob(patch) {
  state.job = { ...(state.job || {}), ...patch };
  updateStatus();
  const { path } = parseHash();
  if (path === '/knowledge') {
    const panel = view.querySelector('.steps')?.closest('.card');
    // 進捗だけの更新は画面全体を描き直さない
    if (panel && state.job.running) {
      import('./views/knowledge.js').then(({ jobPanel }) => {
        const tmp = document.createElement('div');
        tmp.innerHTML = String(jobPanel(state.job));
        panel.replaceWith(tmp.firstElementChild);
      });
    } else render({ keepScroll: true });
  }
}

let abort = null;

/** ブラウザから直接 LLM を呼ぶときの設定。トークンはコンパニオンの /llm 中継を使うときだけ送る */
function directLlmOptions(ai) {
  return { ...ai, token: /\/llm\/?$/.test(ai.baseUrl) ? ai.token : '' };
}

async function runAnalysis(mode = 'analyze') {
  if (state.job?.running) return;
  const ai = state.settings.ai;
  if (ai.mode === 'direct') {
    if (!ai.chatModel) return toast('チャットモデルを設定してください（設定 → AI）');
    abort = new AbortController();
    setJob({ running: true, where: 'browser', stage: 'embed', message: '開始しています', done: 0, total: 0, error: '' });
    try {
      const llm = createLlmClient(directLlmOptions(ai));
      const onProgress = (p) => setJob(p);
      if (mode === 'recommend') {
        state.analysis.recommendations = await recommendBooks({ library: state.library, analysis: state.analysis, llm, signal: abort.signal, onProgress });
        state.analysis.recommendedAt = new Date().toISOString();
        state.analysis.recommendationNote = recommendationNote(state.analysis.recommendations);
      } else {
        const cache = await loadCache();
        try {
          const { analysis } = await analyzeLibrary({ library: state.library, llm, cache, signal: abort.signal, onProgress });
          state.analysis = analysis;
        } finally {
          await saveCache(cache);
        }
      }
      await save.analysis();
      setJob({ running: false, stage: 'done', message: '完了しました' });
      toast('分析が完了しました');
      scheduleFolderExport();
    } catch (e) {
      setJob({ running: false, stage: 'error', error: e.message, message: '' });
    }
    return;
  }
  // PC のコンパニオンサーバで実行（先に同期して最新の点を渡す）
  setJob({ running: true, where: 'pc', stage: 'embed', message: 'PC と同期しています', done: 0, total: 0, error: '' });
  try {
    await syncWithPc();
    await companion.startAnalyze(mode, state.settings.root);
    await pollPcJob();
  } catch (e) {
    setJob({ running: false, stage: 'error', error: e.message, message: '' });
  }
}

let followingPcJob = false;

/** PC の分析を最後まで追う。通信が途切れても再接続を続け、長く途切れたら「状況不明」にして確認ボタンを出す */
async function pollPcJob() {
  if (followingPcJob) return;
  followingPcJob = true;
  try {
    const result = await followJob({
      fetchJob: () => companion.job(),
      onUpdate: (u) => {
        if (u.job) setJob({ ...u.job, where: 'pc', running: u.job.running, lost: false, reconnecting: 0 });
        else setJob({ where: 'pc', running: true, lost: false, reconnecting: u.reconnecting, message: `PC との通信が途切れました。再接続しています（${u.reconnecting}/${u.maxFailures - 1}）` });
      },
    });
    if (result.lost) return setJob({ running: false, lost: true, where: 'pc', error: '', message: '' });
    if (result.error) return;
    let analysis;
    try {
      analysis = await companion.analysis();
    } catch {
      return setJob({ running: false, lost: true, where: 'pc', error: '', message: '' });
    }
    if (analysis) {
      state.analysis = analysis;
      await save.analysis();
    }
    setJob({ running: false, lost: false, stage: 'done', message: result.vault ? `完了しました（Obsidian: ${result.vault.written} 件を書き込み）` : '完了しました' });
    toast('分析が完了しました');
    refreshPcInfo();
    scheduleFolderExport();
  } finally {
    followingPcJob = false;
  }
}

/** 「PC の状況を確認」: 途切れていた分析の続きを追う（終わっていれば結果を受け取る） */
function checkPcJob() {
  setJob({ running: true, lost: false, where: 'pc', message: 'PC に分析の状況を問い合わせています' });
  return pollPcJob();
}

async function cancelAnalysis() {
  if (state.job?.where === 'pc') await companion.cancel().catch(() => {});
  abort?.abort();
}

// ---- 同期 ----

async function sync({ quiet = false } = {}) {
  try {
    const { analysisDir } = await syncWithPc();
    if (!quiet) toast(`PC と同期しました${analysisDir ? `（分析: ${analysisDir}）` : ''}`);
    render({ keepScroll: true });
    // PC は同期のあと自動で Vault に書き出すので、少し待ってから結果（最後に書き出した時刻）を取り直す
    refreshPcInfo();
    setTimeout(refreshPcInfo, 2500);
    scheduleFolderExport();
    // PC で分析が走っていれば進捗を追う
    const job = await companion.job().catch(() => null);
    if (job?.running && !state.job?.running) {
      setJob({ ...job, where: 'pc' });
      pollPcJob().catch((e) => setJob({ running: false, error: e.message }));
    }
  } catch (e) {
    if (!quiet) toast(e.message, 5000);
  }
}

/** PC の状態（出力先・最後に Vault に書き出した結果）を取り直し、表示している画面に反映する */
async function refreshPcInfo() {
  if (state.settings.ai.mode !== 'companion') return;
  try {
    state.pcInfo = await companion.info();
  } catch {
    return;
  }
  const { path } = parseHash();
  if (path === '/export' || path === '/settings') render({ keepScroll: true });
}

let folderTimer;
/** Vault のフォルダを選んである PC のブラウザでは、取り込み・同期・編集のあとに自動で書き出す（許可を求める画面は出さない） */
function scheduleFolderExport() {
  if (!fsSupported || !state.settings.autoExportFolder) return;
  clearTimeout(folderTimer);
  folderTimer = setTimeout(async () => {
    try {
      const handle = await savedVault();
      if (!handle || (await handle.queryPermission({ mode: 'readwrite' })) !== 'granted') return;
      const plan = await writeVaultFs(handle, vaultFiles, state.settings.root);
      await recordFolderExport(handle, plan, 'folder');
    } catch {
      /* 次の機会に書き出す */
    }
  }, 1500);
}

async function recordFolderExport(handle, plan, trigger) {
  state.folderExport = { at: new Date().toISOString(), trigger, name: handle.name, root: state.settings.root, written: plan.writes.length - 1, unchanged: plan.unchanged, deleted: plan.deletes.length };
  await save.folderExport();
  const { path } = parseHash();
  if (path === '/export') render({ keepScroll: true });
}

let syncTimer;
function autoSyncAfterChange() {
  scheduleFolderExport();
  if (state.settings.ai.mode !== 'companion' || !state.settings.autoSync) return;
  clearTimeout(syncTimer);
  syncTimer = setTimeout(() => sync({ quiet: true }), 1500);
}

// ---- Obsidian へ書き出し ----

function vaultFiles(owners = {}) {
  return renderVault(state.library, state.analysis, { root: state.settings.root, owners });
}

function showExportResult(text, ok = true) {
  const out = view.querySelector('#export-result');
  if (out) out.innerHTML = String(html`<p class="notice ${ok ? 'ok' : 'err'}" style="margin-top:12px">${text}</p>`);
  toast(text, 4000);
}

function planSummary(plan) {
  const written = plan.writes.length - 1;
  return `書き込み ${written} 件・変更なし ${plan.unchanged} 件${plan.deletes.length ? `・削除 ${plan.deletes.length} 件` : ''}${plan.skipped.length ? `・同名ノートのため見送り ${plan.skipped.length} 件` : ''}`;
}

// ---- 操作 ----

const actions = {
  async fav(el) {
    const h = updateHighlight(state.library, el.dataset.id, { favorite: !state.library.highlights[el.dataset.id].favorite });
    await persistLibrary();
    el.classList.toggle('on', h.favorite);
    el.textContent = h.favorite ? '★' : '☆';
    el.setAttribute('aria-pressed', String(h.favorite));
    autoSyncAfterChange();
  },
  edit(el) {
    const h = state.library.highlights[el.dataset.id];
    openSheet(
      html`<h2>メモ・タグ</h2>
        <p class="quote">${h.text}</p>
        <label class="field"><span>自分のメモ（Obsidian にも書き出されます）</span><textarea name="userNote">${h.userNote || ''}</textarea></label>
        <label class="field"><span>タグ（空白かカンマ区切り）</span><input type="text" name="tags" value="${(h.tags || []).join(' ')}" placeholder="例: 習慣 仕事"></label>
        <div class="row spread"><button class="btn danger" value="delete">この点を削除</button><span class="row"><button class="btn" value="cancel">やめる</button><button class="btn primary" value="save">保存</button></span></div>`,
      async (data, action) => {
        if (action === 'delete') {
          if (!confirm('この点を削除しますか？（再取り込みしても戻りません）')) return true;
          updateHighlight(state.library, h.id, { deleted: true });
          toast('削除しました');
        } else {
          updateHighlight(state.library, h.id, { userNote: String(data.get('userNote') || '').trim(), tags: String(data.get('tags') || '').split(/[\s,、]+/) });
        }
        await persistLibrary();
        render({ keepScroll: true });
        autoSyncAfterChange();
      },
    );
  },
  async copy(el) {
    const h = state.library.highlights[el.dataset.id];
    const b = state.library.books[h.bookId];
    await navigator.clipboard.writeText(`${h.text}\n— ${b.title}${b.author ? `（${b.author}）` : ''}`);
    toast('コピーしました');
  },
  async 'delete-book'(el) {
    const b = state.library.books[el.dataset.id];
    if (!confirm(`『${b.title}』とその点をすべて削除しますか？`)) return;
    deleteBook(state.library, b.id);
    await persistLibrary();
    location.hash = '#/books';
    toast('削除しました');
    autoSyncAfterChange();
  },
  async 'load-sample'() {
    const stats = mergeParsed(state.library, SAMPLE_BOOKS);
    await persistLibrary();
    toast(`サンプルを入れました（点 ${stats.added} 件）`);
    location.hash = '#/';
    render();
  },
  shuffle() {
    shuffle++;
    render({ keepScroll: true });
  },
  'run-analysis': () => runAnalysis('analyze'),
  'rerun-recommend': () => runAnalysis('recommend'),
  'cancel-analysis': cancelAnalysis,
  'check-pc-job': () => checkPcJob(),
  async 'rec-feedback'(el) {
    const r = state.analysis?.recommendations?.[Number(el.dataset.i)];
    if (!r) return;
    const f = setFeedback(state.library, { title: r.title, author: r.author }, el.dataset.status);
    await persistLibrary();
    toast(f.status ? `「${r.title}」を「${FEEDBACK_LABELS[f.status]}」にしました。次のおすすめに反映します` : '反応を外しました');
    render({ keepScroll: true });
    autoSyncAfterChange();
  },
  'map-zoom'(el) {
    const svg = document.getElementById('knowledge-map');
    const w = parseFloat(svg.getAttribute('width')) || 100;
    const next = Math.max(100, Math.min(400, w * (el.dataset.dir === '1' ? 1.5 : 1 / 1.5)));
    svg.setAttribute('width', `${next}%`);
  },
  async 'export-zip'() {
    const files = vaultFiles();
    const zip = createZip(files.map((f) => ({ name: f.path, content: f.content })));
    download(`obsidian-${state.settings.root}-${new Date().toISOString().slice(0, 10)}.zip`, zip, 'application/zip');
    showExportResult(`${files.length} 件のノートを zip にしました`);
  },
  async 'export-fs'() {
    try {
      const handle = (await savedVault()) || (await pickVault());
      const plan = await writeVaultFs(handle, vaultFiles, state.settings.root);
      await recordFolderExport(handle, plan, 'manual');
      showExportResult(`「${handle.name}」に書き出しました: ${planSummary(plan)}`);
    } catch (e) {
      if (e.name !== 'AbortError') showExportResult(e.message, false);
    }
  },
  async 'pick-vault'() {
    try {
      const handle = await pickVault();
      toast(`Vault: ${handle.name}`);
    } catch {
      /* キャンセル */
    }
  },
  async 'export-pc'() {
    try {
      await syncWithPc();
      const r = await companion.exportVault(state.settings.root);
      await refreshPcInfo();
      showExportResult(`PC の Vault（${r.vaultPath}）に書き出しました: 書き込み ${r.written} 件・変更なし ${r.unchanged} 件${r.skipped.length ? `・同名ノートのため見送り ${r.skipped.length} 件` : ''}`);
    } catch (e) {
      showExportResult(e.message, false);
    }
  },
  sync: () => sync(),
  async 'toggle-autosync'(el) {
    state.settings.autoSync = el.checked;
    await save.settings();
  },
  async 'toggle-autoexport-folder'(el) {
    state.settings.autoExportFolder = el.checked;
    await save.settings();
  },
  async backup() {
    const data = JSON.stringify(makeBackup(state.library, state.analysis));
    download(`book-highlights-backup-${new Date().toISOString().slice(0, 10)}.json`, data, 'application/json');
  },
  async 'clear-all'() {
    if (!confirm('この端末のハイライト・分析結果・設定をすべて消します。よろしいですか？')) return;
    await kv.clear();
    state.library = emptyLibrary();
    state.analysis = null;
    location.hash = '#/';
    location.reload();
  },
  async 'copy-bookmarklet'() {
    await navigator.clipboard.writeText(await buildBookmarklet());
    toast('ブックマークレットをコピーしました。ブックマークの URL に貼り付けてください');
  },
};

const forms = {
  search(form) {
    form.querySelector('input')?.blur();
  },
  'book-filter'(form) {
    const q = new FormData(form).get('q');
    const { query } = parseHash();
    if (q) query.set('q', q);
    else query.delete('q');
    location.hash = `#/books?${query}`;
  },
  async 'export-settings'(form) {
    const d = new FormData(form);
    state.settings.root = safeFileName(String(d.get('root') || '').trim() || 'Highlights');
    state.settings.vaultName = String(d.get('vaultName') || '').trim();
    await save.settings();
    toast('保存しました');
    render({ keepScroll: true });
  },
  async 'ai-settings'(form, submitter) {
    const d = new FormData(form);
    const ai = state.settings.ai;
    Object.assign(ai, {
      mode: d.get('mode') === 'direct' ? 'direct' : 'companion',
      companionUrl: String(d.get('companionUrl') || '').trim(),
      token: String(d.get('token') || ''),
      baseUrl: String(d.get('baseUrl') || '').trim() || 'http://localhost:11434',
      chatModel: String(d.get('chatModel') || '').trim(),
      embedModel: String(d.get('embedModel') || '').trim(),
    });
    await save.settings();
    const out = view.querySelector('#ai-test');
    if (submitter?.value !== 'test') {
      toast('保存しました');
      return;
    }
    out.innerHTML = '<p class="loading">確認中…</p>';
    try {
      if (ai.mode === 'direct') {
        const models = await createLlmClient({ ...directLlmOptions(ai), timeoutMs: 15000 }).listModels();
        view.querySelector('#model-list').innerHTML = models.map((m) => `<option value="${m.replace(/"/g, '&quot;')}">`).join('');
        out.innerHTML = String(html`<p class="notice ok">接続できました。モデル: ${models.join('、') || '（なし）'}</p>`);
      } else {
        const info = await companion.info();
        out.innerHTML = String(html`<p class="notice ${info.llm.configured ? 'ok' : ''}">PC に接続できました。点 ${info.stats.highlights} 件・チャットモデル: ${info.llm.chatModel || '未設定（PC で bh config model …）'}・埋め込み: ${info.llm.embedModel || '文字 n-gram'}・Vault: ${info.vault ? '設定済み' : '未設定'}</p>`);
      }
    } catch (e) {
      out.innerHTML = String(html`<p class="notice err">${e.message}</p>`);
    }
  },
};

document.addEventListener('click', (e) => {
  const el = e.target.closest('[data-action]');
  if (!el || el.tagName === 'INPUT') return;
  const fn = actions[el.dataset.action];
  if (!fn) return;
  e.preventDefault();
  Promise.resolve(fn(el)).catch((err) => toast(err.message, 5000));
});

document.addEventListener('change', (e) => {
  const el = e.target.closest('input[data-action]');
  if (el) actions[el.dataset.action]?.(el);
});

document.addEventListener('submit', (e) => {
  const form = e.target.closest('form[data-form]');
  if (!form) return;
  e.preventDefault();
  Promise.resolve(forms[form.dataset.form]?.(form, e.submitter)).catch((err) => toast(err.message, 5000));
});

window.addEventListener('hashchange', () => render());

document.addEventListener('visibilitychange', () => {
  const job = state.job;
  if (document.visibilityState === 'visible' && job?.where === 'pc' && (job.lost || job.running) && !followingPcJob) checkPcJob();
});

async function start() {
  await loadState();
  state.servedByCompanion = await detectServedByCompanion();
  render();
  listenBookmarklet();
  requestPersistence();
  if ('serviceWorker' in navigator && window.isSecureContext) navigator.serviceWorker.register('sw.js').catch(() => {});
  if (state.settings.ai.mode === 'companion' && state.settings.autoSync && (state.servedByCompanion || state.settings.ai.companionUrl)) sync({ quiet: true });
}

start().catch((e) => {
  view.innerHTML = String(html`<p class="notice err">起動できませんでした: ${e.message}</p>`);
});
