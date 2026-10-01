// 取り込み結果をライブラリと分析結果に反映する（Web アプリ・`bh import`・コンパニオンサーバで共通）
import { mergeLibraries, mergeParsed } from './model.js';

export const BACKUP_FORMAT = 'book-highlights/backup';

/** 分析結果の新しさ（分析し直した時刻とおすすめを選び直した時刻の新しい方）。同期でも使う */
export function analysisStamp(analysis) {
  return [analysis?.createdAt || '', analysis?.recommendedAt || ''].sort().pop();
}

/** バックアップファイルの中身（ライブラリと分析結果を分けて持つ） */
export function makeBackup(library, analysis) {
  return { format: BACKUP_FORMAT, version: 1, exportedAt: new Date().toISOString(), library, analysis: analysis || null };
}

/**
 * parseFiles() の結果を取り込む。
 * - バックアップのライブラリは mergeLibraries で統合（自分の編集は新しい方が残る）
 * - バックアップの分析結果は、手元より新しいときだけ採用
 * - 各形式のハイライトは mergeParsed で取り込む（reviveDeleted: false なら削除した本を復活させない。ブラウザ拡張の自動取り込み用）
 * 戻り値: { library, analysis, stats, analysisChanged }（引数のライブラリは変更しない）
 */
export function applyImport({ library, analysis = null }, { books = [], backups = [] }, { now, reviveDeleted = true } = {}) {
  let lib = structuredClone(library);
  let an = analysis;
  for (const b of backups) {
    lib = mergeLibraries(lib, b.library);
    if (b.analysis && analysisStamp(b.analysis) > analysisStamp(an)) an = b.analysis;
  }
  const parsed = mergeParsed(lib, books, { ...(now ? { now } : {}), reviveDeleted });
  const highlights = Object.values(lib.highlights).filter((h) => !h.deleted).length;
  return { library: lib, analysis: an, stats: { ...parsed, backups: backups.length, highlights }, analysisChanged: an !== analysis };
}
