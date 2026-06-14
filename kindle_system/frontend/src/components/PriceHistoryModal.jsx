import React, { useEffect, useRef, useState } from 'react';

const SERVER = 'http://localhost:8765';

function formatDate(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const min = String(d.getMinutes()).padStart(2, '0');
  return `${mm}/${dd} ${hh}:${min}`;
}

function drawChart(canvas, history) {
  if (!canvas || history.length === 0) return;
  const dpr = window.devicePixelRatio || 1;
  const W   = canvas.clientWidth;
  const H   = canvas.clientHeight;
  canvas.width  = W * dpr;
  canvas.height = H * dpr;
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);

  const PAD = { top: 20, right: 24, bottom: 52, left: 68 };
  const cw = W - PAD.left - PAD.right;
  const ch = H - PAD.top  - PAD.bottom;

  // 有効データのみ（null / 0 はKU対象の可能性があるため除外しない）
  const prices   = history.map((h) => h.actual_price ?? h.sell_price ?? 0);
  const minPrice = Math.min(...prices);
  const maxPrice = Math.max(...prices);
  const priceRange = maxPrice - minPrice || 1;

  const toX = (i) => PAD.left + (i / Math.max(history.length - 1, 1)) * cw;
  const toY = (p) => PAD.top  + ch - ((p - minPrice) / priceRange) * ch;

  // ─── グリッド線 ─────────────────────────────────────────────────
  ctx.strokeStyle = 'rgba(148,163,184,0.18)';
  ctx.lineWidth = 1;
  const gridLines = 4;
  for (let i = 0; i <= gridLines; i++) {
    const y = PAD.top + (i / gridLines) * ch;
    ctx.beginPath();
    ctx.moveTo(PAD.left, y);
    ctx.lineTo(PAD.left + cw, y);
    ctx.stroke();
    // Y軸ラベル
    const val = Math.round(maxPrice - (i / gridLines) * priceRange);
    ctx.fillStyle = '#94a3b8';
    ctx.font = '10px Inter, sans-serif';
    ctx.textAlign = 'right';
    ctx.fillText(`¥${val.toLocaleString()}`, PAD.left - 8, y + 4);
  }

  // ─── 面グラデーション ────────────────────────────────────────────
  const grad = ctx.createLinearGradient(0, PAD.top, 0, PAD.top + ch);
  grad.addColorStop(0, 'rgba(37,99,235,0.20)');
  grad.addColorStop(1, 'rgba(37,99,235,0.01)');

  ctx.beginPath();
  history.forEach((h, i) => {
    const x = toX(i);
    const y = toY(h.actual_price ?? h.sell_price ?? 0);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.lineTo(toX(history.length - 1), PAD.top + ch);
  ctx.lineTo(PAD.left, PAD.top + ch);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();

  // ─── 折れ線 ─────────────────────────────────────────────────────
  ctx.beginPath();
  ctx.strokeStyle = '#2563eb';
  ctx.lineWidth   = 2;
  ctx.lineJoin    = 'round';
  history.forEach((h, i) => {
    const x = toX(i);
    const y = toY(h.actual_price ?? h.sell_price ?? 0);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.stroke();

  // ─── データポイント ─────────────────────────────────────────────
  history.forEach((h, i) => {
    const x = toX(i);
    const y = toY(h.actual_price ?? h.sell_price ?? 0);
    ctx.beginPath();
    ctx.arc(x, y, 3.5, 0, Math.PI * 2);
    ctx.fillStyle   = '#2563eb';
    ctx.strokeStyle = '#fff';
    ctx.lineWidth   = 1.5;
    ctx.fill();
    ctx.stroke();
  });

  // ─── X軸ラベル（最大8件抜粋） ────────────────────────────────────
  const step = Math.max(1, Math.ceil(history.length / 8));
  ctx.fillStyle  = '#94a3b8';
  ctx.font       = '9px Inter, sans-serif';
  ctx.textAlign  = 'center';
  history.forEach((h, i) => {
    if (i % step !== 0 && i !== history.length - 1) return;
    ctx.save();
    ctx.translate(toX(i), PAD.top + ch + 12);
    ctx.rotate(-Math.PI / 6);
    ctx.fillText(formatDate(h.timestamp), 0, 0);
    ctx.restore();
  });
}

export default function PriceHistoryModal({ asin, title, onClose }) {
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState(null);
  const canvasRef = useRef(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetch(`${SERVER}/api/books/${asin}/history`)
      .then((r) => r.json())
      .then((data) => {
        setHistory(data);
        setLoading(false);
      })
      .catch(() => {
        setError('データの取得に失敗しました。');
        setLoading(false);
      });
  }, [asin]);

  useEffect(() => {
    if (!loading && history.length > 0 && canvasRef.current) {
      drawChart(canvasRef.current, history);
    }
  }, [loading, history]);

  // キーボードで閉じる
  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  // 最新・最小・最高価格
  const latestActual = history.length ? (history[history.length - 1].actual_price ?? '-') : '-';
  const validPrices  = history.map((h) => h.actual_price).filter((p) => p !== null && p !== undefined);
  const minActual    = validPrices.length ? Math.min(...validPrices) : '-';
  const maxActual    = validPrices.length ? Math.max(...validPrices) : '-';

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        {/* ヘッダー */}
        <div className="modal-header">
          <div className="modal-title-area">
            <h2 className="modal-title">{title}</h2>
            <p className="modal-asin">ASIN: {asin}</p>
          </div>
          <button className="modal-close-btn" onClick={onClose} title="閉じる">×</button>
        </div>

        {/* サマリー数値 */}
        {!loading && !error && history.length > 0 && (
          <div className="modal-stats">
            <div className="modal-stat">
              <span className="modal-stat-label">計測回数</span>
              <span className="modal-stat-value">{history.length} 回</span>
            </div>
            <div className="modal-stat">
              <span className="modal-stat-label">最新実質価格</span>
              <span className="modal-stat-value accent">
                {typeof latestActual === 'number' ? `¥${latestActual.toLocaleString()}` : '-'}
              </span>
            </div>
            <div className="modal-stat">
              <span className="modal-stat-label">最安値</span>
              <span className="modal-stat-value success">
                {typeof minActual === 'number' ? `¥${minActual.toLocaleString()}` : '-'}
              </span>
            </div>
            <div className="modal-stat">
              <span className="modal-stat-label">最高値</span>
              <span className="modal-stat-value muted">
                {typeof maxActual === 'number' ? `¥${maxActual.toLocaleString()}` : '-'}
              </span>
            </div>
          </div>
        )}

        {/* チャート / ローディング / エラー */}
        <div className="modal-body">
          {loading && (
            <div className="modal-loading">
              <div className="loading-dots"><span /><span /><span /></div>
              <p>履歴を読み込み中...</p>
            </div>
          )}
          {error && <p className="modal-error">{error}</p>}
          {!loading && !error && history.length === 0 && (
            <p className="modal-empty">価格履歴がまだありません。</p>
          )}
          {!loading && !error && history.length > 0 && (
            <canvas ref={canvasRef} className="price-chart-canvas" />
          )}
        </div>

        {/* 直近の履歴テーブル */}
        {!loading && !error && history.length > 0 && (
          <div className="modal-table-wrap">
            <table className="modal-table">
              <thead>
                <tr>
                  <th>日時</th>
                  <th>販売価格</th>
                  <th>ポイント</th>
                  <th>実質価格</th>
                </tr>
              </thead>
              <tbody>
                {[...history].reverse().slice(0, 20).map((h, idx) => (
                  <tr key={idx}>
                    <td className="modal-td-date">{formatDate(h.timestamp)}</td>
                    <td>{h.sell_price !== null ? `¥${h.sell_price.toLocaleString()}` : '-'}</td>
                    <td>{h.point_value ? `${h.point_value} pt` : '0 pt'}</td>
                    <td className="modal-td-actual">
                      {h.actual_price !== null ? `¥${h.actual_price.toLocaleString()}` : '-'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
