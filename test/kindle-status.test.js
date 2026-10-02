import { test } from 'node:test';
import assert from 'node:assert/strict';
import { kindleSyncState, mergeKindleSync, normalizeKindleReport } from '../web/core/kindle-status.js';

const T0 = '2026-10-01T00:00:00.000Z';
const minutesLater = (iso, m) => new Date(new Date(iso).getTime() + m * 60000).toISOString();

test('Kindle 状態: 報告を検証し、知らないキーは捨てる', () => {
  assert.deepEqual(normalizeKindleReport({ ok: true }), { ok: true, needLogin: false, added: 0, intervalMin: 15, error: '' });
  const r = normalizeKindleReport({ ok: false, needLogin: true, added: 3, intervalMin: '30', error: 'a\u0000b\nc', token: 'secret', books: [] });
  assert.deepEqual(r, { ok: false, needLogin: true, added: 3, intervalMin: 30, error: 'abc' });
  assert.equal(normalizeKindleReport({ ok: false, error: 'あ'.repeat(500) }).error.length, 300);
});

test('Kindle 状態: 不正な報告は例外にする', () => {
  const bad = [
    null,
    [],
    'x',
    {},
    { ok: 'true' },
    { ok: true, needLogin: 'yes' },
    { ok: true, added: -1 },
    { ok: true, added: 1.5 },
    { ok: true, added: 100001 },
    { ok: true, intervalMin: 0 },
    { ok: true, intervalMin: 1441 },
    { ok: true, intervalMin: 'abc' },
    { ok: true, error: 5 },
  ];
  for (const b of bad) assert.throws(() => normalizeKindleReport(b), Error, JSON.stringify(b));
});

test('Kindle 状態: 保存形への反映（最後の成功と最後に新しい点は引き継ぐ）', () => {
  const rep = (o) => ({ ok: true, needLogin: false, added: 0, intervalMin: 15, error: '', ...o });
  const s1 = mergeKindleSync(undefined, rep({ added: 5 }), T0);
  assert.deepEqual(s1, { lastCheck: { at: T0, ok: true, error: '', needLogin: false, added: 5, intervalMin: 15 }, lastSuccessAt: T0, lastNew: { at: T0, added: 5 } });
  const T1 = minutesLater(T0, 15);
  const s2 = mergeKindleSync(s1, rep({ ok: false, error: '失敗' }), T1);
  assert.equal(s2.lastCheck.at, T1);
  assert.equal(s2.lastCheck.ok, false);
  assert.equal(s2.lastSuccessAt, T0);
  assert.deepEqual(s2.lastNew, { at: T0, added: 5 });
  const s3 = mergeKindleSync(undefined, rep({ ok: false }), T0);
  assert.equal(s3.lastSuccessAt, undefined);
  assert.equal(s3.lastNew, undefined);
});

test('Kindle 状態: 表示用の状態の判定', () => {
  const ks = (o = {}, intervalMin = 15) => ({ lastCheck: { at: T0, ok: true, error: '', needLogin: false, added: 0, intervalMin, ...o } });
  assert.equal(kindleSyncState(null, T0), 'none');
  assert.equal(kindleSyncState({}, T0), 'none');
  assert.equal(kindleSyncState(ks(), minutesLater(T0, 1)), 'ok');
  assert.equal(kindleSyncState(ks({ ok: false }), T0), 'error');
  assert.equal(kindleSyncState(ks({ ok: false, needLogin: true }), T0), 'login');
  // 3 × 間隔ちょうどは stale でない。超えたら stale（他より優先）
  assert.equal(kindleSyncState(ks(), minutesLater(T0, 45)), 'ok');
  assert.equal(kindleSyncState(ks(), minutesLater(T0, 46)), 'stale');
  assert.equal(kindleSyncState(ks({ ok: false, needLogin: true }), minutesLater(T0, 46)), 'stale');
  // 間隔が欠けていれば 15 分扱い
  const noInterval = { lastCheck: { at: T0, ok: true } };
  assert.equal(kindleSyncState(noInterval, minutesLater(T0, 45)), 'ok');
  assert.equal(kindleSyncState(noInterval, minutesLater(T0, 46)), 'stale');
});

test('Kindle 状態: 画面に出す文言', async () => {
  const { kindleSyncLines } = await import('../web/js/ui.js');
  const mk = (o = {}, extra = {}) => ({ lastCheck: { at: T0, ok: true, error: '', needLogin: false, added: 0, intervalMin: 15, ...o }, ...extra });
  const at = (m) => minutesLater(T0, m);

  assert.deepEqual(kindleSyncLines(null, T0), ['自動取り込み: まだ拡張から連絡がありません（拡張機能を入れていない場合は、下の手順で設定できます）']);

  const ok = kindleSyncLines(mk(), at(1));
  assert.match(ok[0], /^自動取り込み: 正常（最終確認 .+）。線がノートブックに反映されるまで数分かかることがあります$/);
  assert.equal(ok[1], '最後に新しい点: まだ届いていません');

  const withNew = kindleSyncLines(mk({}, { lastNew: { at: T0, added: 4 } }), at(1));
  assert.match(withNew[1], /^最後に新しい点: .+・4 件$/);

  const login = kindleSyncLines(mk({ ok: false, needLogin: true }), at(1));
  assert.match(login[0], /^自動取り込み: Amazon のログインが切れています（最終確認 .+）。PC のブラウザで read\.amazon\.co\.jp\/notebook にログインしてください$/);

  const err = kindleSyncLines(mk({ ok: false, error: '3 冊を読み取れませんでした' }), at(1));
  assert.match(err[0], /^自動取り込み: 失敗（最終確認 .+）: 3 冊を読み取れませんでした$/);

  const stale = kindleSyncLines(mk(), at(150));
  assert.equal(stale[0], '自動取り込み: 拡張から 2 時間 30 分 連絡がありません。PC のブラウザが閉じているか、PC に送れていない可能性があります');
  assert.match(stale[1], /^最後の結果: 正常（最終確認 .+）$/);
  assert.equal(stale.length, 3);
  assert.equal(kindleSyncLines(mk(), at(50))[0], '自動取り込み: 拡張から 50 分 連絡がありません。PC のブラウザが閉じているか、PC に送れていない可能性があります');
  assert.match(kindleSyncLines(mk(), at(60 * 24 * 3))[0], /^自動取り込み: 拡張から 3 日 連絡がありません/);
});
