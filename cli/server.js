// コンパニオンサーバ（PC で常駐させる小さな HTTP サーバ。依存ライブラリなし）
//
// - Web アプリそのものを配信する（http://localhost:8787 で開けば Safari でも LLM を使える）
// - /api/*  … ライブラリの同期、PC 側での AI 分析ジョブ、Obsidian への書き出し
// - /llm/*  … ローカル LLM（Ollama など）への中継。CORS と Host ヘッダの問題をここで吸収する
//
// スマホからは `tailscale serve --bg 8787` で https://<PC名>.<tailnet>.ts.net として届く。

import { createServer } from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import path from 'node:path';
import { REPO_ROOT, exportAndRecord, readVaultOwners, summarizePlan } from './store.js';
import { safeFileName } from '../web/core/text.js';
import { libraryStats, mergeLibraries } from '../web/core/model.js';
import { applyImport } from '../web/core/importing.js';
import { createLlmClient, normalizeBaseUrl } from '../web/core/analysis/llm.js';
import { analyzeLibrary, recommendBooks, recommendationNote } from '../web/core/analysis/pipeline.js';
import { parseFiles } from '../web/core/parsers/index.js';

const WEB_ROOT = path.join(REPO_ROOT, 'web');
const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.webmanifest': 'application/manifest+json',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.txt': 'text/plain; charset=utf-8',
  '.md': 'text/markdown; charset=utf-8',
};
const MAX_BODY = 50 * 1024 * 1024;

// catalogFetch: おすすめの本を探す書誌 DB への fetch（テストで差し替える）
export { summarizePlan };

export function createCompanionServer({ store, log = console.log, catalogFetch, autoExportDelay = 800 }) {
  // 同期・取り込み・分析結果の保存のあと、少し待ってから Vault を自動で書き出す（続けて来たら 1 回にまとめる）
  let autoTimer = null;
  let autoTrigger = '';
  let autoPending = '';
  function scheduleAutoExport(trigger) {
    autoTrigger = trigger;
    clearTimeout(autoTimer);
    autoTimer = setTimeout(async () => {
      try {
        const cfg = await store.config();
        if (!cfg.vault || cfg.autoExport === false) return;
        // 分析中は書き出しを分析の後に回す（分析が失敗・中止しても、同期した内容は Vault に届く）
        if (job.running) {
          autoPending = autoTrigger;
          return;
        }
        await store.lock(() => exportAndRecord(store, { trigger: autoTrigger }));
      } catch (e) {
        log(`[auto-export] ${e.message}`);
      }
    }, autoExportDelay);
  }

  /** Web アプリから届いた Vault 内のフォルダ名を使い、PC の設定にも残す（自動の書き出しや bh obsidian も同じ場所に） */
  async function resolveRoot(requested, cfg) {
    const root = typeof requested === 'string' && requested.trim() ? safeFileName(requested.trim()) : '';
    if (!root || root === cfg.root) return cfg.root;
    const saved = await store.config();
    await store.saveConfig({ ...saved, root });
    return root;
  }

  const job = { running: false, stage: '', message: '', done: 0, total: 0, error: '', startedAt: null, finishedAt: null, vault: null, controller: null };

  function isAllowedOrigin(origin, host, cfg) {
    if (!origin) return true;
    if (/^https?:\/\/(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$/.test(origin)) return true;
    // Tailscale Serve で配信した画面からの同一オリジンのリクエストだけ許可する
    // （*.ts.net を丸ごと許すと、公開されている他人の Funnel サイトからも読めてしまう）
    if (host && origin === `https://${host}` && /\.ts\.net$/i.test(host)) return true;
    return (cfg.allowedOrigins || []).some((o) => o.replace(/\/+$/, '') === origin);
  }

  function isAllowedHost(host, cfg) {
    // DNS リバインディング対策: 想定外の Host 名で来たリクエストは拒否（トークン設定時は除く）
    if (cfg.token) return true;
    const h = String(host || '').replace(/:\d+$/, '').toLowerCase();
    return ['localhost', '127.0.0.1', '[::1]'].includes(h) || h.endsWith('.ts.net') || (cfg.allowedHosts || []).includes(h);
  }

  async function handle(req, res) {
    const url = new URL(req.url, 'http://localhost');
    const origin = req.headers.origin;
    const cfg = await store.config();
    if (origin && isAllowedOrigin(origin, req.headers.host, cfg)) {
      res.setHeader('Access-Control-Allow-Origin', origin);
      res.setHeader('Vary', 'Origin');
      res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-BH-Token');
      res.setHeader('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS');
      // Chrome の Local Network Access / Private Network Access のプリフライトに応える
      if (req.headers['access-control-request-private-network']) res.setHeader('Access-Control-Allow-Private-Network', 'true');
    } else if (origin) {
      return send(res, 403, { error: `このオリジンからの接続は許可されていません: ${origin}（bh config origin ${origin} で追加できます）` });
    }
    if (req.method === 'OPTIONS') return send(res, 204);
    if (!isAllowedHost(req.headers.host, cfg)) return send(res, 403, { error: 'Host ヘッダが許可されていません' });

    const isApi = url.pathname.startsWith('/api/') || url.pathname.startsWith('/llm/');
    if (isApi && cfg.token && req.headers['x-bh-token'] !== cfg.token && url.searchParams.get('token') !== cfg.token) {
      return send(res, 401, { error: 'トークンが違います' });
    }
    if (url.pathname.startsWith('/llm/')) return proxyLlm(req, res, url, cfg);
    if (url.pathname.startsWith('/api/')) return api(req, res, url, cfg);
    return serveStatic(res, url.pathname);
  }

  async function api(req, res, url, cfg) {
    const route = `${req.method} ${url.pathname}`;
    switch (route) {
      case 'GET /api/info': {
        const lib = await store.library();
        const analysis = await store.analysis();
        return send(res, 200, {
          app: 'book-highlights',
          stats: libraryStats(lib),
          llm: { chatModel: cfg.llm.chatModel, embedModel: cfg.llm.embedModel, configured: Boolean(cfg.llm.chatModel) },
          vault: Boolean(cfg.vault),
          root: cfg.root,
          vaultPath: cfg.vault ? path.join(cfg.vault, cfg.root) : '',
          autoExport: cfg.autoExport !== false,
          lastExport: (await store.state()).lastExport || null,
          owners: cfg.vault ? await readVaultOwners(cfg.vault, cfg.root) : {},
          analysis: analysis ? { createdAt: analysis.createdAt, ...analysis.stats } : null,
          job: publicJob(),
        });
      }
      case 'GET /api/library':
        return send(res, 200, await store.library());
      case 'POST /api/library/merge': {
        const incoming = await readBody(req);
        const merged = await store.lock(async () => {
          const m = mergeLibraries(await store.library(), incoming);
          await store.saveLibrary(m);
          return m;
        });
        scheduleAutoExport('sync');
        return send(res, 200, merged);
      }
      case 'POST /api/import': {
        // { files: [{ name, base64 }], auto? } を PC 側でパースして取り込む（auto: ブラウザ拡張の自動取り込み。削除した本を復活させない）
        const body = await readBody(req);
        const files = (body.files || []).map((f) => ({ name: f.name, bytes: Buffer.from(f.base64, 'base64') }));
        const parsed = await parseFiles(files);
        const r = await store.lock(async () => {
          const out = applyImport({ library: await store.library(), analysis: await store.analysis() }, parsed, { reviveDeleted: !body.auto });
          await store.saveLibrary(out.library);
          if (out.analysisChanged) await store.saveAnalysis(out.analysis);
          return out;
        });
        scheduleAutoExport('import');
        return send(res, 200, { stats: r.stats, results: parsed.results, analysisChanged: r.analysisChanged });
      }
      case 'GET /api/analysis': {
        const a = await store.analysis();
        return a ? send(res, 200, a) : send(res, 404, { error: 'まだ分析していません' });
      }
      case 'PUT /api/analysis': {
        await store.saveAnalysis(await readBody(req));
        scheduleAutoExport('analysis');
        return send(res, 200, { ok: true });
      }
      case 'GET /api/analyze':
        return send(res, 200, publicJob());
      case 'POST /api/analyze': {
        const body = await readBody(req).catch(() => ({}));
        await resolveRoot(body.root, cfg);
        if (!job.running) runJob(body.mode === 'recommend' ? 'recommend' : 'analyze');
        return send(res, 202, publicJob());
      }
      case 'DELETE /api/analyze':
        job.controller?.abort();
        return send(res, 200, publicJob());
      case 'POST /api/obsidian/export': {
        const body = await readBody(req).catch(() => ({}));
        const root = await resolveRoot(body.root, cfg);
        if (!cfg.vault) return send(res, 400, { error: 'PC で Obsidian の Vault フォルダが設定されていません（bh config vault <パス>）' });
        const result = await store.lock(() => exportAndRecord(store, { root, trigger: 'manual' }));
        return send(res, 200, result);
      }
      default:
        return send(res, 404, { error: `不明な API: ${route}` });
    }
  }

  function publicJob() {
    const { controller, ...rest } = job;
    return rest;
  }

  async function runJob(mode) {
    Object.assign(job, { running: true, stage: 'start', message: '開始しています', done: 0, total: 0, error: '', startedAt: new Date().toISOString(), finishedAt: null, vault: null, controller: new AbortController() });
    try {
      const cfg = await store.config();
      if (!cfg.llm.chatModel) throw new Error('チャットモデルが設定されていません（bh config model <モデル名>）');
      const llm = createLlmClient(cfg.llm);
      const library = await store.library();
      const onProgress = (p) => Object.assign(job, p);
      if (mode === 'recommend') {
        const analysis = await store.analysis();
        if (!analysis) throw new Error('先に分析を実行してください');
        analysis.recommendations = await recommendBooks({ library, analysis, llm, onProgress, signal: job.controller.signal, fetchImpl: catalogFetch });
        analysis.recommendedAt = new Date().toISOString();
        analysis.recommendationNote = recommendationNote(analysis.recommendations);
        await store.saveAnalysis(analysis);
      } else {
        const cache = await store.cache();
        let analysis;
        try {
          ({ analysis } = await analyzeLibrary({ library, llm, cache, onProgress, signal: job.controller.signal, options: { fetchImpl: catalogFetch } }));
        } finally {
          await store.saveCache(cache);
        }
        await store.saveAnalysis(analysis);
      }
      if (cfg.vault) {
        job.message = 'Obsidian に書き出しています';
        job.vault = await store.lock(() => exportAndRecord(store, { trigger: 'analysis' }));
      }
      job.stage = 'done';
      job.message = '完了しました';
    } catch (e) {
      job.error = e.message;
      job.stage = 'error';
      log(`[analyze] ${e.message}`);
    } finally {
      job.running = false;
      job.finishedAt = new Date().toISOString();
      // 分析が Vault まで書き出せなかったとき、分析中に届いた同期・取り込みの書き出しをここで行う
      if (autoPending && (job.error || !job.vault)) scheduleAutoExport(autoPending);
      autoPending = '';
    }
  }

  async function proxyLlm(req, res, url, cfg) {
    const upstream = normalizeBaseUrl(cfg.llm.baseUrl) + url.pathname.replace(/^\/llm/, '') + url.search;
    const body = req.method === 'GET' || req.method === 'HEAD' ? undefined : await readRaw(req);
    let r;
    try {
      // fetch が Host を upstream 側に合わせるので、Ollama の Host チェック（403）を回避できる
      r = await fetch(upstream, { method: req.method, headers: { 'Content-Type': req.headers['content-type'] || 'application/json' }, body });
    } catch (e) {
      return send(res, 502, { error: `LLM サーバ (${cfg.llm.baseUrl}) に接続できません: ${e.message}` });
    }
    res.writeHead(r.status, { 'Content-Type': r.headers.get('content-type') || 'application/json' });
    if (r.body) for await (const chunk of r.body) res.write(chunk);
    res.end();
  }

  async function serveStatic(res, pathname) {
    let p;
    try {
      p = decodeURIComponent(pathname);
    } catch {
      return send(res, 400, { error: 'bad request' });
    }
    if (p.endsWith('/')) p += 'index.html';
    const full = path.join(WEB_ROOT, p);
    if (!full.startsWith(WEB_ROOT + path.sep)) return send(res, 403, { error: 'forbidden' });
    try {
      const s = await stat(full);
      if (s.isDirectory()) return serveStatic(res, p + '/');
      res.writeHead(200, { 'Content-Type': MIME[path.extname(full)] || 'application/octet-stream', 'Cache-Control': 'no-cache' });
      res.end(await readFile(full));
    } catch {
      send(res, 404, { error: 'not found' });
    }
  }

  const server = createServer((req, res) => {
    handle(req, res).catch((e) => {
      log(`[error] ${req.method} ${req.url}: ${e.message}`);
      if (!res.headersSent) send(res, e.status || 500, { error: e.message });
      else res.end();
    });
  });
  server.job = job;
  return server;
}


function send(res, status, data) {
  if (data === undefined) {
    res.writeHead(status);
    return res.end();
  }
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' });
  res.end(JSON.stringify(data));
}

async function readRaw(req) {
  const chunks = [];
  let size = 0;
  for await (const c of req) {
    size += c.length;
    if (size > MAX_BODY) throw Object.assign(new Error('リクエストが大きすぎます'), { status: 413 });
    chunks.push(c);
  }
  return Buffer.concat(chunks);
}

async function readBody(req) {
  const raw = await readRaw(req);
  try {
    return JSON.parse(raw.toString('utf8') || '{}');
  } catch {
    throw Object.assign(new Error('JSON を読めませんでした'), { status: 400 });
  }
}
