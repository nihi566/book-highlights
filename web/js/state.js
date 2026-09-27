// アプリの状態（ライブラリ・分析結果・設定）と保存
import { kv } from './db.js';
import { emptyLibrary } from '../core/model.js';
import { deserializeCache, emptyCache, serializeCache } from '../core/analysis/pipeline.js';

export const DEFAULT_SETTINGS = {
  root: 'Highlights',
  vaultName: '',
  ai: {
    // companion: PC のコンパニオンサーバで分析（スマホからも使える） / direct: ブラウザから LLM に直接つなぐ
    mode: 'companion',
    companionUrl: '',
    token: '',
    baseUrl: 'http://localhost:11434',
    chatModel: '',
    embedModel: '',
  },
  autoSync: true,
  // PC のブラウザで Vault のフォルダに直接書き出しているとき、取り込み・同期のあとに自動で書き出す
  autoExportFolder: true,
};

export const state = {
  library: emptyLibrary(),
  analysis: null,
  settings: structuredClone(DEFAULT_SETTINGS),
  servedByCompanion: false,
  job: null,
  lastSync: null,
  // PC のコンパニオンサーバの状態（出力先・最後に Vault に書き出した結果など）
  pcInfo: null,
  // このブラウザから Vault のフォルダに最後に書き出した結果
  folderExport: null,
};

export async function loadState() {
  const [library, analysis, settings, lastSync, folderExport] = await Promise.all([kv.get('library'), kv.get('analysis'), kv.get('settings'), kv.get('lastSync'), kv.get('folderExport')]);
  state.folderExport = folderExport || null;
  if (library) state.library = library;
  if (analysis) state.analysis = analysis;
  if (settings) state.settings = { ...structuredClone(DEFAULT_SETTINGS), ...settings, ai: { ...DEFAULT_SETTINGS.ai, ...(settings.ai || {}) } };
  state.lastSync = lastSync || null;
}

export const save = {
  library: () => kv.set('library', state.library),
  analysis: () => (state.analysis ? kv.set('analysis', state.analysis) : kv.del('analysis')),
  settings: () => kv.set('settings', state.settings),
  lastSync: () => kv.set('lastSync', state.lastSync),
  folderExport: () => kv.set('folderExport', state.folderExport),
};

export async function loadCache() {
  return deserializeCache(await kv.get('cache')) || emptyCache();
}

export async function saveCache(cache) {
  await kv.set('cache', serializeCache(cache));
}
