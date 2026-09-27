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
};

export const state = {
  library: emptyLibrary(),
  analysis: null,
  settings: structuredClone(DEFAULT_SETTINGS),
  servedByCompanion: false,
  job: null,
  lastSync: null,
};

export async function loadState() {
  const [library, analysis, settings, lastSync] = await Promise.all([kv.get('library'), kv.get('analysis'), kv.get('settings'), kv.get('lastSync')]);
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
};

export async function loadCache() {
  return deserializeCache(await kv.get('cache')) || emptyCache();
}

export async function saveCache(cache) {
  await kv.set('cache', serializeCache(cache));
}
