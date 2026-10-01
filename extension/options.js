import { DEFAULTS, isReachableCompanionUrl } from './sync-core.js';

const $ = (id) => document.getElementById(id);
const form = $('settings');

function renderStatus(s) {
  const el = $('status');
  if (!s) {
    el.textContent = 'まだ取り込んでいません';
    el.className = '';
    return;
  }
  const at = new Date(s.at).toLocaleString('ja-JP');
  el.className = s.ok ? 'ok' : 'err';
  el.textContent = s.ok ? `${at} に確認しました（${s.books} 冊中 ${s.fetched} 冊を読み、新しい点 ${s.added} 件）` : `${at} に失敗しました: ${s.error}`;
}

async function load() {
  const { settings, status } = await chrome.storage.local.get(['settings', 'status']);
  const s = { ...DEFAULTS, ...settings };
  for (const [k, v] of Object.entries(s)) if (form.elements[k]) form.elements[k].value = String(v);
  $('origin-cmd').textContent = `bh config origin ${location.origin}`;
  $('notebook-link').href = `https://${s.amazonHost}/notebook`;
  renderStatus(status);
}

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const f = form.elements;
  const companionUrl = f.companionUrl.value.trim().replace(/\/+$/, '');
  if (!isReachableCompanionUrl(companionUrl)) {
    $('saved').textContent = 'URL は http://localhost:… か https://….ts.net にしてください';
    $('saved').className = 'err';
    return;
  }
  const { settings: before } = await chrome.storage.local.get('settings');
  const prev = { ...DEFAULTS, ...before };
  await chrome.storage.local.set({ settings: { companionUrl, token: f.token.value.trim(), amazonHost: f.amazonHost.value, intervalMin: Number(f.intervalMin.value) } });
  // 送り先や Amazon を変えたら、新しい送り先に全ての本が届くよう前回の記録を消す
  if (prev.companionUrl !== companionUrl || prev.amazonHost !== f.amazonHost.value) await chrome.storage.local.remove(['known', 'lastTopDate']);
  $('notebook-link').href = `https://${f.amazonHost.value}/notebook`;
  $('saved').textContent = '保存しました';
  $('saved').className = 'ok';
});

async function run(type, button) {
  button.disabled = true;
  $('status').textContent = '取り込んでいます…';
  $('status').className = '';
  try {
    renderStatus(await chrome.runtime.sendMessage({ target: 'background', type }));
  } finally {
    button.disabled = false;
  }
}

$('sync-now').addEventListener('click', (e) => run('sync-now', e.currentTarget));
$('reset').addEventListener('click', (e) => run('reset', e.currentTarget));
chrome.storage.onChanged.addListener((changes) => changes.status && renderStatus(changes.status.newValue));

load();
