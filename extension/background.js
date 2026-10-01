// 定期的に Kindle ノートブックを確認し、新しいハイライトを PC の bh serve へ送る
import { DEFAULTS, chunk, importBody, isReachableCompanionUrl, pickBooksToFetch } from './sync-core.js';

const ALARM = 'kindle-sync';
const BATCH = 20; // この冊数ごとに送る（途中で止まっても、送り終えた分は次回に読み直さない）

async function settings() {
  const { settings: s } = await chrome.storage.local.get('settings');
  return { ...DEFAULTS, ...s };
}

async function schedule() {
  const s = await settings();
  await chrome.alarms.clear(ALARM);
  chrome.alarms.create(ALARM, { periodInMinutes: Number(s.intervalMin) || DEFAULTS.intervalMin, delayInMinutes: 1 });
}

let creating;
async function ensureOffscreen() {
  const found = await chrome.runtime.getContexts({ contextTypes: ['OFFSCREEN_DOCUMENT'] });
  if (found.length) return;
  creating ??= chrome.offscreen.createDocument({ url: 'offscreen.html', reasons: ['DOM_PARSER'], justification: 'Kindle ノートブックの HTML からハイライトを読み取る' }).finally(() => (creating = null));
  await creating;
}

async function askOffscreen(msg) {
  await ensureOffscreen();
  const r = await chrome.runtime.sendMessage({ target: 'offscreen', ...msg });
  if (r?.needLogin) throw Object.assign(new Error('Amazon にログインしていません。ブラウザで Kindle のノートブックを開いてログインしてください。'), { needLogin: true });
  if (!r || r.error) throw new Error(r?.error || 'ノートブックを読み取れませんでした');
  return r;
}

async function postToCompanion(s, books) {
  const headers = { 'content-type': 'application/json' };
  if (s.token) headers['x-bh-token'] = s.token;
  let r;
  try {
    r = await fetch(`${s.companionUrl.replace(/\/+$/, '')}/api/import`, { method: 'POST', headers, body: JSON.stringify(importBody(books)), signal: AbortSignal.timeout(60000) });
  } catch {
    throw new Error(`PC（${s.companionUrl}）に接続できません。bh serve が動いているか確認してください。`);
  }
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error || `PC から HTTP ${r.status} が返りました`);
  return body.stats || {};
}

async function setStatus(status) {
  await chrome.storage.local.set({ status });
  await chrome.action.setBadgeText({ text: status.ok ? '' : '!' });
  if (!status.ok) await chrome.action.setBadgeBackgroundColor({ color: '#b3261e' });
}

/** 読み終えた本の日付を記録する（他の処理が消した直後でも、その時点の最新の記録に足す） */
async function markKnown(books) {
  const { known = {} } = await chrome.storage.local.get('known');
  for (const b of books) known[b.asin] = b.lastAnnotated;
  await chrome.storage.local.set({ known });
}

async function runSync() {
  const s = await settings();
  const at = new Date().toISOString();
  let added = 0;
  let fetched = 0;
  const failed = [];
  try {
    if (!isReachableCompanionUrl(s.companionUrl)) throw new Error('PC の URL は http://localhost:… か https://….ts.net にしてください。');
    const { known = {}, lastTopDate = '' } = await chrome.storage.local.get(['known', 'lastTopDate']);
    const { books } = await askOffscreen({ type: 'library', host: s.amazonHost });
    const targets = pickBooksToFetch(books, known, { lastTopDate });
    for (const group of chunk(targets, BATCH)) {
      const done = [];
      for (const b of group) {
        try {
          b.highlights = (await askOffscreen({ type: 'highlights', host: s.amazonHost, asin: b.asin })).highlights;
          done.push(b);
        } catch (e) {
          if (e.needLogin) throw e;
          // 1 冊が読めなくても残りは続ける。読めなかった本は記録しないので次回また読む
          failed.push(b.title || b.asin);
        }
        await new Promise((res) => setTimeout(res, 200));
      }
      if (done.some((b) => b.highlights.length)) added += (await postToCompanion(s, done)).added || 0;
      fetched += done.length;
      await markKnown(done);
    }
    if (!failed.length) await chrome.storage.local.set({ lastTopDate: books[0]?.lastAnnotated || '' });
    const error = failed.length ? `${failed.length} 冊を読み取れませんでした（${failed.slice(0, 3).join('、')}${failed.length > 3 ? ' ほか' : ''}）。次回もう一度読みます。` : '';
    await setStatus({ ok: !failed.length, at, added, fetched, books: books.length, error });
  } catch (e) {
    await setStatus({ ok: false, at, added, fetched, error: e.message, needLogin: Boolean(e.needLogin) });
  }
  return (await chrome.storage.local.get('status')).status;
}

let running;
function syncNow() {
  running ??= runSync().finally(() => (running = null));
  return running;
}

/** 記録を消して全ての本を読み直す（取り込み中なら終わるのを待ってから） */
async function resetAndSync() {
  await running;
  await chrome.storage.local.remove(['known', 'lastTopDate']);
  return syncNow();
}

chrome.runtime.onInstalled.addListener(() => {
  schedule();
  chrome.runtime.openOptionsPage();
});
chrome.runtime.onStartup.addListener(schedule);
chrome.alarms.onAlarm.addListener((a) => a.name === ALARM && syncNow());
chrome.action.onClicked.addListener(() => chrome.runtime.openOptionsPage());
chrome.storage.onChanged.addListener((changes) => changes.settings && schedule());

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.target !== 'background') return;
  if (msg.type === 'sync-now') {
    syncNow().then(sendResponse);
    return true;
  }
  if (msg.type === 'reset') {
    resetAndSync().then(sendResponse);
    return true;
  }
});
