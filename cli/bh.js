#!/usr/bin/env node
// book-highlights の PC 用コマンド
//
//   bh import <ファイル...>        Kindle / Play Books のハイライトを取り込む
//   bh obsidian                    Obsidian の Vault に書き出す
//   bh analyze                     ローカル LLM で 点→線→面→立体 を分析し、おすすめの本を選ぶ
//   bh recommend                   おすすめの本だけ選び直す
//   bh serve                       コンパニオンサーバを起動（Web アプリ + 同期 + LLM 中継 + Play ブックスの自動取り込み）
//   bh google login|sync|logout    Play ブックスのメモ（Google ドライブ）との連携
//   bh list / bh search <語>       一覧・検索
//   bh config [キー 値]            設定の表示・変更

import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { createStore, exportAndRecord } from './store.js';
import { createCompanionServer } from './server.js';
import { createGoogleClient, describeSync, isFolderId, MIN_INTERVAL_SEC, startDriveWatcher } from './google.js';
import { SOURCES, listBooks, libraryStats, searchHighlights } from '../web/core/model.js';
import { applyImport } from '../web/core/importing.js';
import { parseFiles } from '../web/core/parsers/index.js';
import { createLlmClient } from '../web/core/analysis/llm.js';
import { analyzeLibrary, recommendBooks, recommendationNote } from '../web/core/analysis/pipeline.js';
import { truncate } from '../web/core/text.js';

const HELP = `使い方: bh <コマンド> [オプション]

  import <ファイル...> [--no-obsidian]  ハイライトを取り込む（My Clippings.txt / Kindle のエクスポート HTML /
                                      ブックマークレットの JSON / Play Books のメモ .docx .html .md / それらの .zip）。
                                      Vault を設定していれば続けて書き出す
  obsidian [--dry-run]                Obsidian の Vault に書き出す
  analyze [--no-recommend]            ローカル LLM で 点→線→面→立体 を分析（結果は Vault にも書き出す）
  recommend                           おすすめの本を選び直す
  serve [--port 8787] [--host 127.0.0.1]  コンパニオンサーバを起動（Google にログイン済みなら Play ブックスの線を自動で取り込む）
  google login                        Google にログインする（ドライブの読み取りだけを許可）
  google sync                         Play ブックスのメモを今すぐ取り込む
  google logout                       ログアウトする（Google 側の許可も取り消す）
  list                                本の一覧
  search <語...>                       ハイライトを検索
  config                              設定を表示
  config vault <パス>                 Obsidian の Vault フォルダ
  config root <フォルダ名>            Vault 内の出力先（既定: Highlights）
  config url <URL>                    LLM サーバ（既定: http://127.0.0.1:11434 = Ollama）
  config model <名前>                 チャットモデル（例: qwen3.5:9b）
  config embed <名前>                 埋め込みモデル（例: bge-m3。空なら文字 n-gram で代用）
  config origin <URL>                 接続を許可する Web アプリのオリジン（GitHub Pages など）を追加
  config token <文字列>               API にトークンを要求する（インターネットに公開する場合は必須）
  config google-client <ID> <シークレット>  Google Cloud で作った OAuth クライアント（種類: デスクトップ アプリ）
  config google-folder <フォルダ ID>  「Play ブックスのメモ」フォルダを名前で探せないときに指定（空で自動に戻す）
  config google-interval <秒>         ドライブを確認する間隔（既定: 60、最短 ${MIN_INTERVAL_SEC}）
  config autoexport on|off            同期・取り込み・分析のあとに Vault を自動で書き出す（既定: on）

環境変数 BH_DATA でデータの保存先（既定: リポジトリの data/）を変えられます。`;

function parseArgs(argv) {
  const args = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith('--')) {
      const [k, v] = a.slice(2).split('=');
      if (v !== undefined) args[k] = v;
      else if (argv[i + 1] && !argv[i + 1].startsWith('--') && ['port', 'host', 'data'].includes(k)) args[k] = argv[++i];
      else args[k] = true;
    } else args._.push(a);
  }
  return args;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const [cmd, ...rest] = args._;
  const store = createStore(args.data);

  switch (cmd) {
    case 'import': {
      if (!rest.length) throw new Error('取り込むファイルを指定してください');
      const files = await Promise.all(rest.map(async (p) => ({ name: path.basename(p), bytes: new Uint8Array(await readFile(p)) })));
      const parsed = await parseFiles(files);
      for (const r of parsed.results) {
        if (r.error) console.log(`✗ ${r.name}: ${r.error}`);
        else console.log(`✓ ${r.name}: ${r.formatLabel} — 本 ${r.books} 冊 / 点 ${r.highlights} 件`);
      }
      const r = applyImport({ library: await store.library(), analysis: await store.analysis() }, parsed);
      await store.saveLibrary(r.library);
      if (r.analysisChanged) await store.saveAnalysis(r.analysis);
      const s = r.stats;
      console.log(`取り込み: 新しい点 ${s.added} 件、更新 ${s.updated} 件、既存 ${s.unchanged} 件（新しい本 ${s.booksAdded} 冊）${r.analysisChanged ? '。バックアップの新しい分析結果も反映しました' : ''}`);
      // Vault が設定されていれば、取り込んだらすぐ Obsidian にも写す（--no-obsidian で止める）
      const cfgAfter = await store.config();
      if (!args['no-obsidian'] && (args.obsidian || (cfgAfter.vault && cfgAfter.autoExport !== false))) {
        try {
          await exportVault(store, { trigger: 'import' });
        } catch (e) {
          // 取り込みは保存済みなので失敗扱いにはしない
          console.log(`! 取り込みは保存しましたが、Obsidian への書き出しに失敗しました: ${e.message}（あとで bh obsidian で書き出せます）`);
        }
      }
      break;
    }
    case 'obsidian':
      await exportVault(store, { dryRun: Boolean(args['dry-run']) });
      break;
    case 'analyze':
    case 'recommend': {
      const cfg = await store.config();
      if (!cfg.llm.chatModel) throw new Error('チャットモデルが未設定です。例: bh config model qwen3.5:9b');
      const llm = createLlmClient(cfg.llm);
      const library = await store.library();
      const onProgress = progressPrinter();
      if (cmd === 'recommend') {
        const analysis = await store.analysis();
        if (!analysis) throw new Error('先に bh analyze を実行してください');
        analysis.recommendations = await recommendBooks({ library, analysis, llm, onProgress });
        analysis.recommendedAt = new Date().toISOString();
        analysis.recommendationNote = recommendationNote(analysis.recommendations);
        await store.saveAnalysis(analysis);
        printRecommendations(analysis);
      } else {
        const cache = await store.cache();
        let analysis;
        try {
          ({ analysis } = await analyzeLibrary({ library, llm, cache, onProgress, options: { recommend: !args['no-recommend'] } }));
        } finally {
          // 途中で失敗しても、済んだ部分の LLM の結果は次回に使えるよう保存する
          await store.saveCache(cache);
        }
        await store.saveAnalysis(analysis);
        process.stdout.write('\n');
        console.log(`\n■ 立体: ${analysis.solid.title}\n${analysis.solid.core}\n`);
        for (const p of analysis.planes) {
          console.log(`■ 面: ${p.name}`);
          for (const id of p.lineIds) {
            const l = analysis.lines.find((x) => x.id === id);
            console.log(`   ─ 線: ${l.name}（点 ${l.highlightIds.length}）`);
          }
        }
        printRecommendations(analysis);
      }
      if (cfg.vault && cfg.autoExport !== false) await exportVault(store, { trigger: 'analysis' });
      else if (cfg.vault) console.log('自動書き出しがオフのため、Obsidian には書き出していません（bh obsidian で書き出せます）');
      break;
    }
    case 'serve': {
      const cfg = await store.config();
      const port = Number(args.port || cfg.port);
      const host = args.host || cfg.host;
      const drive = startDriveWatcher({ store, client: createGoogleClient({ store }) });
      const server = createCompanionServer({ store, drive });
      server.listen(port, host, () => {
        console.log(`コンパニオンサーバ: http://${host === '0.0.0.0' ? 'localhost' : host}:${port}`);
        console.log(`  LLM: ${cfg.llm.baseUrl}（チャット: ${cfg.llm.chatModel || '未設定'} / 埋め込み: ${cfg.llm.embedModel || '文字 n-gram'}）`);
        console.log(`  Vault: ${cfg.vault || '未設定'}`);
        console.log(`  Play ブックス: ${cfg.google.clientId ? `${Math.max(MIN_INTERVAL_SEC, cfg.google.intervalSec)} 秒ごとに Google ドライブを確認` : '未設定（docs/setup.md の「Play ブックスの自動取り込み」）'}`);
        console.log(`  スマホから使うには: tailscale serve --bg ${port}`);
      });
      break;
    }
    case 'google': {
      const client = createGoogleClient({ store });
      const [sub] = rest;
      if (sub === 'login') {
        // 取り込みはここでは行わない（動いている bh serve と同時に library.json を書くと、片方の更新が消えるため）
        await client.login();
        console.log('ログインしました。bh serve が次の確認から Play ブックスの線を取り込みます（bh serve を使わない場合は bh google sync）');
      } else if (sub === 'sync') {
        const r = await client.sync();
        console.log(describeSync(r));
        for (const e of r.errors) console.log(`  ! ${e}`);
      } else if (sub === 'logout') {
        await client.logout();
        console.log('ログアウトしました');
      } else throw new Error('使い方: bh google login | sync | logout');
      break;
    }
    case 'list': {
      const lib = await store.library();
      const s = libraryStats(lib);
      console.log(`本 ${s.books} 冊 / 点 ${s.highlights} 件`);
      for (const b of listBooks(lib)) console.log(`${String(b.count).padStart(4)}  ${b.title}${b.author ? ' — ' + b.author : ''}  [${b.sources.map((x) => SOURCES[x]).join(', ')}]`);
      break;
    }
    case 'search': {
      const lib = await store.library();
      const hits = searchHighlights(lib, rest.join(' '));
      for (const h of hits.slice(0, 50)) console.log(`『${lib.books[h.bookId].title}』 ${truncate(h.text.replace(/\s+/g, ' '), 120)}`);
      console.log(`${hits.length} 件`);
      break;
    }
    case 'config': {
      const cfg = await store.config();
      const [key, ...vals] = rest;
      const value = vals.join(' ');
      if (!key) {
        const hidden = (v) => (v ? '(設定済み)' : '');
        console.log(JSON.stringify({ ...cfg, token: hidden(cfg.token), google: { ...cfg.google, clientSecret: hidden(cfg.google.clientSecret) } }, null, 2));
        console.log(`データ: ${store.dataDir}`);
        const last = (await store.state()).lastExport;
        console.log(last ? `最後に Vault に書き出した時刻: ${new Date(last.at).toLocaleString('ja-JP')}（${last.trigger}${last.error ? ` / 失敗: ${last.error}` : ` / 書き込み ${last.written} 件`}）` : '最後に Vault に書き出した時刻: まだ書き出していません');
        break;
      }
      const setters = {
        vault: () => (cfg.vault = path.resolve(value)),
        root: () => (cfg.root = value || 'Highlights'),
        url: () => (cfg.llm.baseUrl = value),
        model: () => (cfg.llm.chatModel = value),
        embed: () => (cfg.llm.embedModel = value),
        origin: () => (cfg.allowedOrigins = [...new Set([...(cfg.allowedOrigins || []), value.replace(/\/+$/, '')])]),
        token: () => (cfg.token = value),
        port: () => (cfg.port = Number(value)),
        host: () => (cfg.host = value),
        'google-client': () => {
          const [clientId, clientSecret = ''] = vals;
          if (!clientId) throw new Error('使い方: bh config google-client <クライアント ID> <クライアント シークレット>');
          Object.assign(cfg.google, { clientId, clientSecret });
        },
        'google-folder': () => {
          if (value && !isFolderId(value)) throw new Error('フォルダ ID の形式ではありません（ドライブでフォルダを開いた URL の folders/ の後ろの文字列）');
          cfg.google.folderId = value;
        },
        'google-interval': () => {
          const sec = Number(value);
          if (!Number.isFinite(sec) || sec < MIN_INTERVAL_SEC) throw new Error(`${MIN_INTERVAL_SEC} 秒以上を指定してください`);
          cfg.google.intervalSec = sec;
        },
        autoexport: () => (cfg.autoExport = !/^(off|false|no|0)$/i.test(value)),
      };
      if (!setters[key]) throw new Error(`不明な設定: ${key}`);
      setters[key]();
      await store.saveConfig(cfg);
      console.log(`${key} を設定しました`);
      break;
    }
    default:
      console.log(HELP);
  }
}

async function exportVault(store, { dryRun = false, trigger = 'manual' } = {}) {
  const cfg = await store.config();
  if (!cfg.vault) throw new Error('Obsidian の Vault フォルダが設定されていません（bh config vault <パス>）');
  const s = await exportAndRecord(store, { trigger, dryRun });
  console.log(`${dryRun ? '[dry-run] ' : ''}Obsidian: 書き込み ${s.written} / 変更なし ${s.unchanged} / 削除 ${s.deleted}（${s.vaultPath}）`);
  for (const p of s.skipped) console.log(`  ! 同名のノートがあるため上書きしませんでした: ${p}`);
  for (const p of s.orphaned) console.log(`  ・ 分析から外れましたが、自分のメモがあるので残しました: ${p}`);
}

function progressPrinter() {
  let last = '';
  return ({ message, done, total }) => {
    const line = total > 1 && !/\d+\/\d+/.test(message) ? `${message} ${done}/${total}` : message;
    if (line === last) return;
    last = line;
    if (process.stdout.isTTY) process.stdout.write(`\r\x1b[K${line}`);
    else console.log(line);
  };
}

function printRecommendations(analysis) {
  if (analysis.recommendationNote) console.log(`\n! ${analysis.recommendationNote}`);
  if (!analysis.recommendations?.length) return;
  console.log('\n■ おすすめの本（✓ = 書誌データベースで実在を確認 / ? = 見つからず）');
  for (const r of analysis.recommendations) {
    const mark = r.verified ? '✓' : r.verified === false ? '?' : ' ';
    console.log(` ${mark} ${r.title}${r.author ? ' — ' + r.author : ''}${r.verified?.publishedDate ? `（${String(r.verified.publishedDate).slice(0, 4)}）` : ''}\n     ${r.reason}`);
  }
}

main().catch((e) => {
  console.error(`エラー: ${e.message}`);
  process.exit(1);
});
