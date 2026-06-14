import React from 'react';

const SORT_OPTIONS = [
  { key: 'discount', label: '割引率順' },
  { key: 'updated',  label: '更新順'   },
  { key: 'price',    label: '実質価格順' },
];

const FILTER_OPTIONS = [
  { key: 'unlimited',  label: 'Unlimited' },
  { key: 'campaign',   label: 'キャンペーン' },
  { key: 'unpurchased',label: '未購入'    },
  { key: 'purchased',  label: '購入済み'  },
];

export default function FilterToolbar({ sortMode, setSortMode, filterMode, toggleFilter, resultCount }) {
  return (
    <div className="toolbar">
      <div className="toolbar-section">
        <span className="toolbar-label">並び替え</span>
        <div className="btn-group">
          {SORT_OPTIONS.map(({ key, label }) => (
            <button
              key={key}
              className={`btn-chip ${sortMode === key ? 'active' : ''}`}
              onClick={() => setSortMode(key)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="toolbar-section">
        <span className="toolbar-label">絞り込み</span>
        <div className="btn-group">
          {FILTER_OPTIONS.map(({ key, label }) => (
            <button
              key={key}
              className={`btn-chip ${filterMode === key ? 'active' : ''}`}
              onClick={() => toggleFilter(key)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <span className="result-count">{resultCount} 件</span>
    </div>
  );
}
