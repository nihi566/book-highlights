import React, { useState, useEffect } from 'react';
import './index.css';

import Header       from './components/Header.jsx';
import ControlPanel from './components/ControlPanel.jsx';
import TerminalLog  from './components/TerminalLog.jsx';
import SummaryCards from './components/SummaryCards.jsx';
import FilterToolbar from './components/FilterToolbar.jsx';
import BookTable    from './components/BookTable.jsx';

import { useBooks } from './hooks/useBooks.js';
import { useJob }   from './hooks/useJob.js';

export default function App() {
  const [theme, setTheme] = useState(() => localStorage.getItem('theme') || 'light');

  // テーマ適用
  useEffect(() => {
    const root = document.documentElement;
    if (theme === 'dark') {
      root.classList.add('dark-theme');
    } else {
      root.classList.remove('dark-theme');
    }
    localStorage.setItem('theme', theme);
  }, [theme]);

  const {
    books,
    loading,
    serverError,
    sortMode,
    setSortMode,
    filterMode,
    toggleFilter,
    processedBooks,
    loadData,
    togglePurchase,
  } = useBooks();

  const { running, sseLogs, checkStatus, startJob, stopJob, closeSSE } = useJob(loadData);

  // 初回マウント時にデータ取得 + ジョブ状態確認
  useEffect(() => {
    loadData();
    checkStatus();
    return () => closeSSE();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleRun = async (startIndex) => {
    const result = await startJob(startIndex);
    if (result.error) alert(result.error);
  };

  const handleStop = async () => {
    const result = await stopJob();
    if (result?.error) alert(result.error);
  };

  return (
    <div className="container">
      {/* ヘッダー */}
      <Header
        bookCount={books.length}
        theme={theme}
        onToggleTheme={() => setTheme((t) => (t === 'light' ? 'dark' : 'light'))}
      />

      {/* サーバー未接続の警告 */}
      {serverError && (
        <div className="server-hint">
          サーバーに接続できません。<code>python src/server.py</code> が起動しているか確認してください。
        </div>
      )}

      {/* コントロールパネル */}
      <ControlPanel
        running={running}
        totalBooks={books.length}
        onRun={handleRun}
        onStop={handleStop}
      />

      {/* ターミナルログ（実行中のみ表示） */}
      <TerminalLog logs={sseLogs} visible={running} />

      {/* サマリーカード */}
      <SummaryCards books={books} />

      {/* フィルタ・ソートツールバー */}
      <FilterToolbar
        sortMode={sortMode}
        setSortMode={setSortMode}
        filterMode={filterMode}
        toggleFilter={toggleFilter}
        resultCount={processedBooks.length}
      />

      {/* 書籍テーブル */}
      <BookTable
        books={processedBooks}
        loading={loading}
        onTogglePurchase={togglePurchase}
      />
    </div>
  );
}
