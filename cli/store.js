// PC 側のデータ保存（data/ 以下の JSON ファイル）と Obsidian Vault への書き込み

import { mkdir, readFile, rename, rm, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { emptyLibrary } from '../web/core/model.js';
import { loadVaultOwners, planVaultWrite, renderVault } from '../web/core/obsidian.js';
import { deserializeCache, serializeCache } from '../web/core/analysis/pipeline.js';

export const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

export const DEFAULT_CONFIG = {
  vault: '',
  root: 'Highlights',
  llm: { baseUrl: 'http://127.0.0.1:11434', chatModel: '', embedModel: '' },
  port: 8787,
  host: '127.0.0.1',
  allowedOrigins: ['https://nihi566.github.io'],
  token: '',
  // 同期・取り込み・分析のあとに Vault を自動で書き出す
  autoExport: true,
};

export function createStore(dataDir = process.env.BH_DATA || path.join(REPO_ROOT, 'data')) {
  const file = (name) => path.join(dataDir, name);

  async function readJson(name, fallback) {
    try {
      return JSON.parse(await readFile(file(name), 'utf8'));
    } catch (e) {
      if (e.code === 'ENOENT') return fallback;
      throw new Error(`${file(name)} を読めませんでした: ${e.message}`);
    }
  }

  async function writeJson(name, data) {
    await mkdir(dataDir, { recursive: true });
    const tmp = file(`${name}.${process.pid}.${Math.random().toString(36).slice(2)}.tmp`);
    await writeFile(tmp, JSON.stringify(data, null, 1));
    await rename(tmp, file(name));
  }

  // 読み込み → 変更 → 保存 を直列に行うためのロック（同時リクエストで更新が消えないように）
  let chain = Promise.resolve();
  const lock = (fn) => {
    const run = chain.then(fn, fn);
    chain = run.catch(() => {});
    return run;
  };

  return {
    dataDir,
    lock,
    async config() {
      const c = await readJson('config.json', {});
      return { ...DEFAULT_CONFIG, ...c, llm: { ...DEFAULT_CONFIG.llm, ...(c.llm || {}) } };
    },
    saveConfig: (c) => writeJson('config.json', c),
    library: () => readJson('library.json', emptyLibrary()),
    saveLibrary: (lib) => writeJson('library.json', lib),
    analysis: () => readJson('analysis.json', null),
    saveAnalysis: (a) => writeJson('analysis.json', a),
    async cache() {
      return deserializeCache(await readJson('cache.json', null));
    },
    saveCache: (c) => writeJson('cache.json', serializeCache(c)),
    // 最後に Vault に書き出した結果など、設定ではない状態
    state: () => readJson('state.json', {}),
    saveState: (st) => writeJson('state.json', st),
  };
}

/** Vault に Markdown を書き出す。自分のメモ（bh:end より下）は保持される */
export async function writeVault(vaultDir, library, analysis, { root = 'Highlights', dryRun = false } = {}) {
  if (!vaultDir) throw new Error('Obsidian の Vault フォルダが設定されていません（bh config vault <パス>）');
  if (!existsSync(vaultDir)) throw new Error(`Vault フォルダが見つかりません: ${vaultDir}`);
  const abs = (p) => {
    const full = path.resolve(vaultDir, p);
    if (!full.startsWith(path.resolve(vaultDir) + path.sep)) throw new Error(`不正なパス: ${p}`);
    return full;
  };
  const read = async (p) => {
    try {
      return await readFile(abs(p), 'utf8');
    } catch {
      return null;
    }
  };
  // 前回と同じファイルを同じ本・線・面に使い続ける
  const files = renderVault(library, analysis, { root, owners: await loadVaultOwners(read, root) });
  const plan = await planVaultWrite(files, read, root);
  if (!dryRun) {
    for (const f of plan.writes) {
      await mkdir(path.dirname(abs(f.path)), { recursive: true });
      await writeFile(abs(f.path), f.content);
    }
    for (const p of plan.deletes) await rm(abs(p), { force: true });
  }
  return plan;
}

export function summarizePlan(plan) {
  return { written: plan.writes.length - 1, deleted: plan.deletes.length, unchanged: plan.unchanged, skipped: plan.skipped, orphaned: plan.orphaned };
}

/**
 * 設定の Vault に書き出し、結果（時刻・件数・きっかけ）を state.json の lastExport に残す。
 * Vault が未設定なら何もしない（null を返す）。失敗も lastExport に残してから投げ直す。
 */
export async function exportAndRecord(store, { root, trigger = 'manual', dryRun = false } = {}) {
  const cfg = await store.config();
  if (!cfg.vault) return null;
  const r = root || cfg.root;
  const record = { at: new Date().toISOString(), trigger, root: r, vaultPath: path.join(cfg.vault, r) };
  try {
    const summary = summarizePlan(await writeVault(cfg.vault, await store.library(), await store.analysis(), { root: r, dryRun }));
    if (!dryRun) await store.saveState({ ...(await store.state()), lastExport: { ...record, ...summary, error: '' } });
    return { ...record, ...summary };
  } catch (e) {
    await store.saveState({ ...(await store.state()), lastExport: { ...record, error: e.message } });
    throw e;
  }
}

