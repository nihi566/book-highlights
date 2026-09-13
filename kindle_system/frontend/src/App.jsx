import React, { useState, useEffect } from 'react';
import './index.css';

import Header          from './components/Header.jsx';
import ControlPanel    from './components/ControlPanel.jsx';
import TerminalLog     from './components/TerminalLog.jsx';
import SummaryCards    from './components/SummaryCards.jsx';
import FilterToolbar   from './components/FilterToolbar.jsx';
import BookTable       from './components/BookTable.jsx';
import PriceHistoryModal from './components/PriceHistoryModal.jsx';

import { useBooks } from './hooks/useBooks.js';
import { useJob }   from './hooks/useJob.js';

export default function App() {
  const [historyAsin,  setHistoryAsin]  = useState(null);
  const [historyTitle, setHistoryTitle] = useState('');

  const {
    books,
    loading,
    serverError,
    sortMode,
    setSortMode,
    filterMode,
    toggleFilter,
    processedBooks,
    displayedBooks,
    hasMore,
    loadMore,
    loadData,
    togglePurchase,
    toggleWant,
  } = useBooks();

  const { running, sseLogs, checkStatus, startJob, startBookmeterSync, startPublish, stopJob, closeSSE } = useJob(loadData);

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

  const handleBookmeterSync = async () => {
    const result = await startBookmeterSync();
    if (result.error) alert(result.error);
  };

  const handlePublish = async () => {
    if (!window.confirm('「読みたい本」を GitHub Pages へ公開します。よろしいですか？')) return;
    const result = await startPublish();
    if (result.error) alert(result.error);
  };

  const openHistory = (asin, title) => {
    setHistoryAsin(asin);
    setHistoryTitle(title);
  };

  const closeHistory = () => {
    setHistoryAsin(null);
    setHistoryTitle('');
  };

  return (
    <div className="container">
      {/* ヘッダー */}
      <Header bookCount={books.length} />

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
        onBookmeterSync={handleBookmeterSync}
        onPublish={handlePublish}
      />

      {/* ターミナルログ（実行中、または直前の実行結果が残っている間は表示） */}
      <TerminalLog logs={sseLogs} visible={running || sseLogs.length > 0} />

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
        books={displayedBooks}
        loading={loading}
        onTogglePurchase={togglePurchase}
        onToggleWant={toggleWant}
        onOpenHistory={openHistory}
        hasMore={hasMore}
        onLoadMore={loadMore}
      />

      {/* 価格推移モーダル */}
      {historyAsin && (
        <PriceHistoryModal
          asin={historyAsin}
          title={historyTitle}
          onClose={closeHistory}
        />
      )}
    </div>
  );
}
