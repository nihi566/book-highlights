// 外部とのやりとり: PC のコンパニオンサーバ、Obsidian の Vault フォルダ、ブックマークレット
import { kv } from './db.js';
import { state, save } from './state.js';
import { mergeLibraries } from '../core/model.js';
import { planVaultWrite } from '../core/obsidian.js';

// ---- コンパニオンサーバ ----

export function companionBase() {
  const url = state.settings.ai.companionUrl.trim().replace(/\/+$/, '');
  if (url) return url;
  return state.servedByCompanion ? location.origin : 'http://localhost:8787';
}

async function call(path, { method = 'GET', body, base = companionBase() } = {}) {
  const headers = { 'Content-Type': 'application/json' };
  if (state.settings.ai.token) headers['X-BH-Token'] = state.settings.ai.token;
  let res;
  try {
    res = await fetch(base + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch (e) {
    throw new Error(`PC（${base}）に接続できません。コンパニオンサーバ（bh serve）が起動しているか確認してください。`);
  }
  const text = await res.text();
  let data;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!res.ok) {
    const err = new Error(data?.error || `HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return data;
}

export const companion = {
  info: (base) => call('/api/info', { base }),
  library: () => call('/api/library'),
  merge: (library) => call('/api/library/merge', { method: 'POST', body: library }),
  analysis: () => call('/api/analysis').catch((e) => (e.status === 404 ? null : Promise.reject(e))),
  putAnalysis: (analysis) => call('/api/analysis', { method: 'PUT', body: analysis }),
  startAnalyze: (mode = 'analyze') => call('/api/analyze', { method: 'POST', body: { mode } }),
  job: () => call('/api/analyze'),
  cancel: () => call('/api/analyze', { method: 'DELETE' }),
  exportVault: () => call('/api/obsidian/export', { method: 'POST', body: {} }),
};

/** 同一オリジンでコンパニオンサーバが動いているか（http://localhost:8787 で開いた場合など） */
export async function detectServedByCompanion() {
  try {
    const res = await fetch('api/info', { headers: state.settings.ai.token ? { 'X-BH-Token': state.settings.ai.token } : {} });
    if (!res.ok && res.status !== 401) return false;
    if (res.status === 401) return true;
    return (await res.json())?.app === 'book-highlights';
  } catch {
    return false;
  }
}

/** PC と同期: ライブラリは双方向に統合、分析結果は新しい方を採用 */
export async function syncWithPc() {
  const merged = await companion.merge(state.library);
  state.library = mergeLibraries(state.library, merged);
  await save.library();
  const remote = await companion.analysis();
  const local = state.analysis;
  let analysisDir = '';
  if (remote && (!local || (remote.createdAt || '') > (local.createdAt || '') || (remote.recommendedAt || '') > (local.recommendedAt || ''))) {
    state.analysis = remote;
    await save.analysis();
    analysisDir = 'pc→この端末';
  } else if (local && (!remote || (local.createdAt || '') > (remote.createdAt || ''))) {
    await companion.putAnalysis(local);
    analysisDir = 'この端末→pc';
  }
  state.lastSync = new Date().toISOString();
  await save.lastSync();
  return { analysisDir };
}

// ---- Obsidian の Vault に直接書き込む（PC の Chrome / Edge。File System Access API） ----

export const fsSupported = typeof window !== 'undefined' && 'showDirectoryPicker' in window;

export async function pickVault() {
  const handle = await window.showDirectoryPicker({ id: 'bh-vault', mode: 'readwrite' });
  await kv.set('vaultHandle', handle);
  return handle;
}

export async function savedVault() {
  const handle = await kv.get('vaultHandle');
  return handle || null;
}

async function ensurePermission(handle) {
  if ((await handle.queryPermission({ mode: 'readwrite' })) === 'granted') return true;
  return (await handle.requestPermission({ mode: 'readwrite' })) === 'granted';
}

async function resolve(dir, path, create) {
  const parts = path.split('/');
  const name = parts.pop();
  let d = dir;
  for (const p of parts) d = await d.getDirectoryHandle(p, { create });
  return { dir: d, name };
}

export async function writeVaultFs(handle, files, root) {
  if (!(await ensurePermission(handle))) throw new Error('フォルダへの書き込みが許可されませんでした');
  const plan = await planVaultWrite(
    files,
    async (p) => {
      try {
        const { dir, name } = await resolve(handle, p, false);
        return await (await (await dir.getFileHandle(name)).getFile()).text();
      } catch {
        return null;
      }
    },
    root,
  );
  for (const f of plan.writes) {
    const { dir, name } = await resolve(handle, f.path, true);
    const w = await (await dir.getFileHandle(name, { create: true })).createWritable();
    await w.write(f.content);
    await w.close();
  }
  for (const p of plan.deletes) {
    try {
      const { dir, name } = await resolve(handle, p, false);
      await dir.removeEntry(name);
    } catch {
      /* 既に無い */
    }
  }
  return plan;
}

// ---- ブックマークレット ----

export async function buildBookmarklet() {
  const src = await (await fetch('bookmarklet/kindle-notebook.js')).text();
  const appUrl = location.origin + location.pathname;
  const code = src
    .split('\n')
    .filter((l) => !/^\s*\/\//.test(l))
    .join('\n')
    .replaceAll('__APP_URL__', appUrl);
  return 'javascript:' + encodeURIComponent(code);
}

export function download(name, data, type = 'application/octet-stream') {
  const url = URL.createObjectURL(new Blob([data], { type }));
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}
