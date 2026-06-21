import React, { useState } from 'react';

export default function ControlPanel({ running, totalBooks, onRun, onStop }) {
  const [startIndex, setStartIndex] = useState('1');
  const [showError, setShowError] = useState(false);
  const [errorText, setErrorText] = useState('');

  const handleRun = () => {
    const clean = startIndex.trim();
    if (!clean || isNaN(Number(clean)) || Number(clean) < 1) {
      setErrorText('1 以上の数値を入力してください');
      setShowError(true);
      setTimeout(() => setShowError(false), 2500);
      return;
    }
    onRun(clean);
  };

  return (
    <div className="control-panel">
      <div className="control-panel-inner">
        {/* 開始位置入力 */}
        <div className={`start-index-wrapper ${showError ? 'error' : ''}`}>
          <span className="start-index-label">開始位置</span>
          <input
            id="start-index-input"
            type="number"
            className="start-index-input"
            min="1"
            max={totalBooks || 9999}
            placeholder={`1 ~ ${totalBooks || '?'}`}
            value={startIndex}
            onChange={(e) => setStartIndex(e.target.value)}
            disabled={running}
          />
          <span className={`start-index-error ${showError ? 'visible' : ''}`}>{errorText}</span>
        </div>

        {/* 実行ボタン */}
        <button
          id="btn-run"
          className="btn btn-run"
          onClick={handleRun}
          disabled={running}
        >
          {running && <span className="spinner" />}
          今すぐ更新
        </button>

        {/* 停止ボタン（実行中のみ表示） */}
        {running && (
          <button id="btn-stop" className="btn btn-stop" onClick={onStop}>
            停止
          </button>
        )}
      </div>
    </div>
  );
}
