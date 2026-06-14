import React from 'react';

export default function Header({ bookCount }) {
  return (
    <header className="app-header">
      <div className="header-brand">
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
      </div>
    </header>
  );
}
