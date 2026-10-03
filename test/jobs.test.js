import { test } from 'node:test';
import assert from 'node:assert/strict';
import { followJob, pcJobOutcome } from '../web/core/jobs.js';

const T1 = '2026-10-03T10:00:00.000Z';
const T2 = '2026-10-03T11:00:00.000Z';
const run = (seq) => followJob({ fetchJob: async () => seq.shift(), sleep: async () => {} });

test('PC の分析の結果: サーバが再起動して初期状態（running:false, stage:\'\'）が返ったら完了扱いにしない', async () => {
  const job = await run([{ running: true, stage: 'lines', startedAt: T1 }, { running: false, stage: '', startedAt: null }]);
  assert.equal(pcJobOutcome(job, T1), 'interrupted');
  assert.equal(pcJobOutcome(job), 'interrupted', '始めた時刻が分からなくても stage が done でなければ中断');
});

test('PC の分析の結果: 自分が始めたのと別のジョブが終わっていたら完了扱いにしない', () => {
  assert.equal(pcJobOutcome({ running: false, stage: 'done', startedAt: T2 }, T1), 'interrupted');
});

test('PC の分析の結果: 自分のジョブが done なら完了、error なら失敗', () => {
  assert.equal(pcJobOutcome({ running: false, stage: 'done', startedAt: T1 }, T1), 'done');
  assert.equal(pcJobOutcome({ running: false, stage: 'done', startedAt: T1 }), 'done');
  assert.equal(pcJobOutcome({ running: false, stage: 'error', error: '中止しました', startedAt: T1 }, T1), 'error');
});
