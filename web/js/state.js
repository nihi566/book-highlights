// アプリの状態（ライブラリ・分析結果・設定）と保存
import { kv } from './db.js';
import { emptyLibrary } from '../core/model.js';
import { deserializeCache, emptyCache, serializeCache } from '../core/analysis/pipeline.js';

export const DEFAULT_SETTINGS = {
  root: 'Highlights',
  // 利用者が「Vault 内のフォルダ名」を自分で決めたか（決めていなければ PC の設定に合わせ、PC へは送らない）
  rootExplicit: false,
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
  // 最後の書き出しでの「ファイル → 本の ID」（「Obsidian で開く」で同じノートを開くため）
  vaultOwners: {},
};

export async function loadState() {
  const [library, analysis, settings, lastSync, folderExport, vaultOwners] = await Promise.all([kv.get('library'), kv.get('analysis'), kv.get('settings'), kv.get('lastSync'), kv.get('folderExport'), kv.get('vaultOwners')]);
  state.folderExport = folderExport || null;
  state.vaultOwners = vaultOwners || {};
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
  vaultOwners: () => kv.set('vaultOwners', state.vaultOwners),
};

export async function loadCache() {
  return deserializeCache(await kv.get('cache')) || emptyCache();
}

export async function saveCache(cache) {
  await kv.set('cache', serializeCache(cache));
}
