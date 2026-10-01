// PC で動いている分析ジョブの進み具合を追う（スマホの通信が一時的に途切れても待ち続ける）

/**
 * fetchJob() を一定間隔で呼び、ジョブが終わるまで待つ。
 * 通信に失敗しても間隔を広げて再接続を試み、maxFailures 回続けて失敗したら { lost: true } を返す
 * （PC では分析が続いている可能性があるので「失敗」とは扱わない）。
 * onUpdate には { job } か { reconnecting, maxFailures, error } が届く。
 */
export async function followJob({ fetchJob, onUpdate = () => {}, sleep = (ms) => new Promise((r) => setTimeout(r, ms)), interval = 1500, maxFailures = 8, signal } = {}) {
  let failures = 0;
  for (;;) {
    if (signal?.aborted) return { stopped: true };
    let job;
    try {
      job = await fetchJob();
      failures = 0;
    } catch (e) {
      failures++;
      if (failures >= maxFailures) return { lost: true, error: e.message };
      onUpdate({ reconnecting: failures, maxFailures, error: e.message });
      await sleep(Math.min(30000, interval * 2 ** failures));
      continue;
    }
    onUpdate({ job });
    if (!job.running) return job;
    await sleep(interval);
  }
}
