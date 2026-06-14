import React, { useState, useEffect, useRef } from 'react';
import './App.css';

export default function App() {
  const [books, setBooks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [sseLogs, setSseLogs] = useState([]);
  const [startIndex, setStartIndex] = useState('1');
  const [showError, setShowError] = useState(false);
  const [errorText, setErrorText] = useState('');
  const [serverError, setServerError] = useState(false);

  // ソート・フィルタ状態
  const [sortMode, setSortMode] = useState('updated'); // discount, updated
  const [filterMode, setFilterMode] = useState('all'); // all, unlimited, purchased, unpurchased

  // テーマ状態（デフォルトはライトモード）
  const [theme, setTheme] = useState(() => {
    return localStorage.getItem('theme') || 'light';
  });

  const sseRef = useRef(null);
  const terminalRef = useRef(null);

  // 1. テーマの適用
  useEffect(() => {
    const root = document.documentElement;
    if (theme === 'dark') {
      root.classList.add('dark-theme');
    } else {
      root.classList.remove('dark-theme');
    }
    localStorage.setItem('theme', theme);
  }, [theme]);

  // 2. 初期データ取得
  const loadData = async () => {
    try {
      const res = await fetch('/api/books');
      if (res.ok) {
        const data = await res.json();
        setBooks(data);
        setServerError(false);
      } else {
        setServerError(true);
      }
    } catch (e) {
      setServerError(true);
    } finally {
      setLoading(false);
    }
  };

  const checkStatus = async () => {
    try {
      const res = await fetch('/api/status');
      if (res.ok) {
        const data = await res.json();
        if (data.running) {
          setRunning(true);
          startSSE();
        }
      }
    } catch (e) {
      // 起動直後などでサーバーがつながらない場合
    }
  };

  useEffect(() => {
    loadData();
    checkStatus();
    return () => {
      if (sseRef.current) sseRef.current.close();
    };
  }, []);

  // 3. ログの自動スクロールは不要のため削除

  // 4. SSE (Server-Sent Events) の監視
  const startSSE = () => {
    if (sseRef.current) sseRef.current.close();

    const sse = new EventSource('/api/events');
    sseRef.current = sse;

    sse.onmessage = (e) => {
      setSseLogs((prev) => [...prev, e.data]);
    };

    sse.addEventListener('done', () => {
      setSseLogs((prev) => [...prev, '完了しました。データを再読み込みしています...']);
      setRunning(false);
      sse.close();
      sseRef.current = null;
      loadData(); // クロール完了後に書籍データをリロード
    });

    sse.onerror = () => {
      setRunning(false);
      if (sseRef.current) {
        sseRef.current.close();
        sseRef.current = null;
      }
    };
  };

  // 5. ジョブ制御 (Run/Sync/Stop)
  const handleRun = async (sync = false) => {
    const cleanIndex = startIndex.trim();
    if (!cleanIndex) {
      setErrorText('★ 開始インデックスの入力が必須です');
      setShowError(true);
      setTimeout(() => setShowError(false), 2000);
      return;
    }

    setSseLogs([]);
    setRunning(true);

    try {
      const endpoint = sync ? 'sync-run' : 'run';
      const res = await fetch(`/api/${endpoint}?start=${encodeURIComponent(cleanIndex)}`, {
        method: 'POST',
      });
      const data = await res.json();
      if (data.ok) {
        startSSE();
      } else {
        setRunning(false);
        alert(data.message || 'ジョブの開始に失敗しました。');
      }
    } catch (e) {
      setRunning(false);
      setServerError(true);
    }
  };

  const handleStop = async () => {
    try {
      const res = await fetch('/api/stop', { method: 'POST' });
      const data = await res.json();
      if (!data.ok) {
        alert(data.message);
      }
    } catch (e) {
      alert('サーバー通信に失敗しました。');
    }
  };

  // 6. 購入ステータスのトグル (楽観的アップデート)
  const handleTogglePurchase = async (asin, currentStatus) => {
    const nextStatus = currentStatus === 1 ? 0 : 1;

    // UI状態を先行して更新 (楽観的更新)
    setBooks((prevBooks) =>
      prevBooks.map((b) => (b.asin === asin ? { ...b, is_purchased: nextStatus } : b))
    );

    try {
      const res = await fetch(`/api/purchase?asin=${encodeURIComponent(asin)}&status=${nextStatus}`, {
        method: 'POST',
      });
      const data = await res.json();
      if (!data.ok) {
        // 失敗した場合は巻き戻す
        setBooks((prevBooks) =>
          prevBooks.map((b) => (b.asin === asin ? { ...b, is_purchased: currentStatus } : b))
        );
        alert('ステータスの更新に失敗しました。');
      }
    } catch (e) {
      // 失敗した場合は巻き戻す
      setBooks((prevBooks) =>
        prevBooks.map((b) => (b.asin === asin ? { ...b, is_purchased: currentStatus } : b))
      );
      alert('サーバーに接続できません。');
    }
  };

  // 7. フロントエンド側でのソート・フィルタの適用
  const filteredAndSortedBooks = () => {
    let result = [...books];

    // フィルタ
    if (filterMode === 'campaign') {
      result = result.filter((b) => b.is_unlimited === 0 && b.campaign_text);
    } else if (filterMode === 'unlimited') {
      result = result.filter((b) => b.is_unlimited === 1);
    } else if (filterMode === 'purchased') {
      result = result.filter((b) => b.is_purchased === 1);
    } else if (filterMode === 'unpurchased') {
      result = result.filter((b) => b.is_purchased === 0);
    }

    // ソート
    if (sortMode === 'discount') {
      result.sort((a, b) => {
        const discountA = a.sell_price ? (a.point_value / a.sell_price) : 0;
        const discountB = b.sell_price ? (b.point_value / b.sell_price) : 0;
        return discountB - discountA;
      });
    } else if (sortMode === 'updated') {
      result.sort((a, b) => (b.timestamp || '').localeCompare(a.timestamp || ''));
    } else if (sortMode === 'unlimited') {
      result.sort((a, b) => (b.is_unlimited || 0) - (a.is_unlimited || 0));
    } else if (sortMode === 'price') {
      result.sort((a, b) => (a.actual_price || 0) - (b.actual_price || 0));
    }

    return result;
  };

  // 8. ログ行の分類用 CSS クラス
  const classifyLine = (text) => {
    if (/^\[Worker-\d+\]\[\d+\/\d+\]/.test(text) || /^\[Worker-\d+\]\s+[A-Za-z0-9]/.test(text)) return 'log-book';
    if (text.includes('[OK]') || text.includes('[Report]')) return 'log-ok';
    if (text.includes('[Error]') || text.includes('[タイムアウト]') || text.includes('[停止]')) return 'log-error';
    if (text.startsWith('アクセス中:') || text.includes('Accessing:')) return 'log-url';
    if (text.includes('[Sleep]')) return 'log-sleep';
    if (/^  \[\d\/\d\]/.test(text) || text.startsWith('  ->')) return 'log-step';
    return '';
  };

  const processedBooks = filteredAndSortedBooks();
  const campaignBooksCount = books.filter((b) => b.is_unlimited === 0 && b.campaign_text).length;

  const toggleFilter = (mode) => {
    setFilterMode((prev) => (prev === mode ? 'all' : mode));
  };

  return (
    <div className="container">
      <header>
        <h1>Kindle Pulse</h1>
        <div className="header-right">
          <span className="timestamp">
            データ総数: {books.length} 件
          </span>
          <button
            className="theme-toggle-btn"
            onClick={() => setTheme((t) => (t === 'light' ? 'dark' : 'light'))}
            title={theme === 'light' ? 'ダークモードへ' : 'ライトモードへ'}
          >
            {theme === 'light' ? 'ダーク' : 'ライト'}
          </button>
        </div>
      </header>

      {/* サーバーエラー警告 */}
      {serverError && (
        <div className="server-hint" style={{ marginBottom: '1.5rem' }}>
          サーバーに接続できません。<code>python src/server.py</code> が起動しているか確認してください。
        </div>
      )}

      {/* コントロールパネル */}
      <div className="control-panel">
        <div className={`start-index-wrapper ${showError ? 'error' : ''}`}>
          <span className="start-index-label">開始位置:</span>
          <input
            type="number"
            className="start-index-input"
            min="1"
            max={books.length || 500}
            placeholder={`1 ~ ${books.length || 500}`}
            value={startIndex}
            onChange={(e) => setStartIndex(e.target.value)}
            disabled={running}
          />
          <span className={`start-index-error ${showError ? 'visible' : ''}`}>{errorText}</span>
        </div>
        
        <button
          className="btn btn-run"
          onClick={() => handleRun(false)}
          disabled={running}
        >
          {running ? <span className="spinner"></span> : ''} 今すぐ更新 (実行のみ)
        </button>

        {running && (
          <button className="btn btn-stop" onClick={handleStop}>
            停止
          </button>
        )}
      </div>

      {/* ターミナルログ */}
      {running && (
        <div className="terminal-wrap">
          <div className="terminal" ref={terminalRef}>
            {sseLogs.map((log, idx) => (
              <p key={idx} className={classifyLine(log)}>
                {log}
              </p>
            ))}
          </div>
          {sseLogs.length > 0 && (
            <span className="term-counter">ログ件数: {sseLogs.length}</span>
          )}
        </div>
      )}

      {/* サマリーカード */}
      <div className="summary-grid" style={{ gridTemplateColumns: 'repeat(2, 1fr)' }}>
        <div className="card">
          <div className="card-title">総監視数</div>
          <div className="card-value">{books.length}</div>
        </div>
        <div className="card">
          <div className="card-title">キャンペーン対象</div>
          <div className="card-value highlight-red">{campaignBooksCount}</div>
        </div>
      </div>

      {/* ツールバー */}
      <div className="controls-wrapper">
        <div className="sort-panel">
          <span className="sort-label">並び替え:</span>
          <button
            className={`btn-sort ${sortMode === 'discount' ? 'active' : ''}`}
            onClick={() => setSortMode('discount')}
          >
            割引率順
          </button>
          <button
            className={`btn-sort ${sortMode === 'updated' ? 'active' : ''}`}
            onClick={() => setSortMode('updated')}
          >
            更新順
          </button>
        </div>

        <div className="filter-panel">
          <span className="sort-label">絞り込み:</span>
          <button
            className={`btn-sort ${filterMode === 'unlimited' ? 'active' : ''}`}
            onClick={() => toggleFilter('unlimited')}
          >
            unlimited対象
          </button>
          <button
            className={`btn-sort ${filterMode === 'purchased' ? 'active' : ''}`}
            onClick={() => toggleFilter('purchased')}
          >
            購入済み
          </button>
          <button
            className={`btn-sort ${filterMode === 'unpurchased' ? 'active' : ''}`}
            onClick={() => toggleFilter('unpurchased')}
          >
            未購入
          </button>
        </div>
      </div>

      {/* 書籍データテーブル */}
      <div className="table-container">
        {loading ? (
          <div className="empty-state">データをロード中...</div>
        ) : processedBooks.length === 0 ? (
          <div className="empty-state">該当する書籍がありません。</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>書籍名 / キャンペーン</th>
                <th>販売価格</th>
                <th>還元ポイント</th>
                <th>実質価格</th>
                <th>購入済み</th>
              </tr>
            </thead>
            <tbody>
              {processedBooks.map((book) => {
                const discountRate = book.sell_price ? Math.round((book.point_value / book.sell_price) * 100) : 0;
                
                // キャンペーン文言整形
                const cleanCampaign = (book.campaign_text || '')
                  .replace('ご購入時にプロモーションが適用されます', '')
                  .split('|')
                  .map(s => s.trim())
                  .filter(Boolean)
                  .join(' | ');

                return (
                  <tr key={book.asin} className={`${book.is_purchased === 1 ? 'purchased-row' : ''} ${cleanCampaign && book.is_unlimited !== 1 ? 'campaign-target' : ''}`}>
                    <td className="col-title">
                      <a href={`https://www.amazon.co.jp/dp/${book.asin}`} target="_blank" rel="noopener noreferrer">
                        {book.title || 'タイトル不明'}
                      </a>
                      
                      {book.is_unlimited === 1 && <span className="badge unlimited">Unlimited対象</span>}
                      {cleanCampaign && book.is_unlimited !== 1 && <span className="badge campaign">キャンペーン</span>}
                      {discountRate >= 20 && <span className="badge discount">{discountRate}% 還元</span>}
                      {book.is_purchased === 1 && <span className="badge purchased">購入済み</span>}

                      {(cleanCampaign || book.timestamp) && (
                        <div className="campaign-text">
                          {book.timestamp && (
                            <span style={{ display: 'inline-block', marginRight: '8px', opacity: 0.8 }}>
                              更新: {book.timestamp.replace('T', ' ').substring(5, 16)}
                            </span>
                          )}
                          {cleanCampaign}
                        </div>
                      )}
                    </td>
                    <td className="col-price">
                      ¥{(book.sell_price || 0).toLocaleString()}
                    </td>
                    <td className="col-point">
                      {book.point_value ? `${book.point_value.toLocaleString()} pt` : '0 pt'} ({discountRate}%)
                    </td>
                    <td className="col-actual">
                      ¥{(book.actual_price || 0).toLocaleString()}
                    </td>
                    <td className="col-purchase">
                      <label className="purchase-toggle">
                        <input
                          type="checkbox"
                          className="purchase-cb"
                          checked={book.is_purchased === 1}
                          onChange={() => handleTogglePurchase(book.asin, book.is_purchased)}
                        />
                        <span className="toggle-track">
                          <span className="toggle-thumb" />
                        </span>
                        <span className="toggle-label">
                          {book.is_purchased === 1 ? '購入済み' : '未購入'}
                        </span>
                      </label>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
