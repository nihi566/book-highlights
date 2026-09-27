// PC 側のデータ保存（data/ 以下の JSON ファイル）と Obsidian Vault への書き込み

import { mkdir, readFile, rename, rm, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { emptyLibrary } from '../web/core/model.js';
import { planVaultWrite, renderVault } from '../web/core/obsidian.js';
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
    const tmp = file(name + '.tmp');
    await writeFile(tmp, JSON.stringify(data, null, 1));
    await rename(tmp, file(name));
  }

  return {
    dataDir,
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
  };
}

/** Vault に Markdown を書き出す。自分のメモ（bh:end より下）は保持される */
export async function writeVault(vaultDir, library, analysis, { root = 'Highlights', dryRun = false } = {}) {
  if (!vaultDir) throw new Error('Obsidian の Vault フォルダが設定されていません（bh config vault <パス>）');
  if (!existsSync(vaultDir)) throw new Error(`Vault フォルダが見つかりません: ${vaultDir}`);
  const files = renderVault(library, analysis, { root });
  const abs = (p) => {
    const full = path.resolve(vaultDir, p);
    if (!full.startsWith(path.resolve(vaultDir) + path.sep)) throw new Error(`不正なパス: ${p}`);
    return full;
  };
  const plan = await planVaultWrite(
    files,
    async (p) => {
      try {
        return await readFile(abs(p), 'utf8');
      } catch {
        return null;
      }
    },
    root,
  );
  if (!dryRun) {
    for (const f of plan.writes) {
      await mkdir(path.dirname(abs(f.path)), { recursive: true });
      await writeFile(abs(f.path), f.content);
    }
    for (const p of plan.deletes) await rm(abs(p), { force: true });
  }
  return plan;
}
