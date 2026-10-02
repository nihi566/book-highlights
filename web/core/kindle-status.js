// ブラウザ拡張（Kindle 自動取り込み）の確認結果を PC に記録し、画面に出すための純粋関数

const DEFAULT_INTERVAL_MIN = 15;
const MAX_ERROR = 300;

/** 拡張から届いた報告を検証して整える。不正なら Error を投げる（知らないキーは捨てる） */
export function normalizeKindleReport(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) throw new Error('報告の形式が正しくありません');
  if (typeof body.ok !== 'boolean') throw new Error('ok は true / false で指定してください');
  const needLogin = body.needLogin ?? false;
  if (typeof needLogin !== 'boolean') throw new Error('needLogin は true / false で指定してください');
  const added = body.added ?? 0;
  if (!Number.isInteger(added) || added < 0 || added > 100000) throw new Error('added は 0〜100000 の整数で指定してください');
  const rawInterval = body.intervalMin ?? DEFAULT_INTERVAL_MIN;
  const intervalMin = typeof rawInterval === 'string' && rawInterval.trim() ? Number(rawInterval) : rawInterval;
  if (!Number.isInteger(intervalMin) || intervalMin < 1 || intervalMin > 1440) throw new Error('intervalMin は 1〜1440 の整数で指定してください');
  const error = body.error ?? '';
  if (typeof error !== 'string') throw new Error('error は文字列で指定してください');
  return { ok: body.ok, needLogin, added, intervalMin, error: error.replace(/[\u0000-\u001f\u007f]/g, '').slice(0, MAX_ERROR) };
}

/** 前回の保存内容に今回の報告を重ねる（最後の成功・最後に新しい点は、今回なかったら前回のまま） */
export function mergeKindleSync(prev, report, now) {
  const { ok, error, needLogin, added, intervalMin } = report;
  const out = { lastCheck: { at: now, ok, error, needLogin, added, intervalMin } };
  const lastSuccessAt = ok ? now : prev?.lastSuccessAt;
  if (lastSuccessAt) out.lastSuccessAt = lastSuccessAt;
  const lastNew = added > 0 ? { at: now, added } : prev?.lastNew;
  if (lastNew) out.lastNew = lastNew;
  return out;
}

/** 'none'（まだ連絡なし）/ 'stale'（長く連絡なし）/ 'login'（ログイン切れ）/ 'error'（失敗）/ 'ok'（正常） */
export function kindleSyncState(ks, now) {
  const last = ks?.lastCheck;
  if (!last) return 'none';
  const limitMs = 3 * (last.intervalMin || DEFAULT_INTERVAL_MIN) * 60000;
  if (new Date(now).getTime() - new Date(last.at).getTime() > limitMs) return 'stale';
  if (last.needLogin) return 'login';
  if (!last.ok) return 'error';
  return 'ok';
}
