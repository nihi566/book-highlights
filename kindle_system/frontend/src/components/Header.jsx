import React from 'react';

export default function Header({ bookCount, theme, onToggleTheme }) {
  return (
    <header className="app-header">
      <div className="header-brand">
        <div className="header-logo">KP</div>
        <div>
          <h1>Kindle Pulse</h1>
          <p className="header-sub">Kindle 価格モニタリングシステム</p>
        </div>
      </div>
      <div className="header-right">
        <span className="data-count-badge">
          <span className="data-count-number">{bookCount}</span>
          <span className="data-count-label">件監視中</span>
        </span>
        <button
          className="theme-toggle-btn"
          onClick={onToggleTheme}
          title={theme === 'light' ? 'ダークモードへ切り替え' : 'ライトモードへ切り替え'}
        >
          {theme === 'light' ? 'ダーク' : 'ライト'}
        </button>
      </div>
    </header>
  );
}
