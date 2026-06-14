import { useState, useCallback } from 'react';

/**
 * 書籍データの取得・ソート・フィルタを管理するカスタムフック
 */
export function useBooks() {
  const [books, setBooks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [serverError, setServerError] = useState(false);
  const [sortMode, setSortMode] = useState('updated');
  const [filterMode, setFilterMode] = useState('all');

  const loadData = useCallback(async () => {
    try {
      const res = await fetch('/api/books');
      if (res.ok) {
        const data = await res.json();
        setBooks(data);
        setServerError(false);
      } else {
        setServerError(true);
      }
    } catch {
      setServerError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  const togglePurchase = useCallback(async (asin, currentStatus) => {
    const nextStatus = currentStatus === 1 ? 0 : 1;
    // 楽観的更新
    setBooks((prev) => prev.map((b) => (b.asin === asin ? { ...b, is_purchased: nextStatus } : b)));
    try {
      const res = await fetch(`/api/purchase?asin=${encodeURIComponent(asin)}&status=${nextStatus}`, {
        method: 'POST',
      });
      const data = await res.json();
      if (!data.ok) {
        // ロールバック
        setBooks((prev) => prev.map((b) => (b.asin === asin ? { ...b, is_purchased: currentStatus } : b)));
      }
    } catch {
      setBooks((prev) => prev.map((b) => (b.asin === asin ? { ...b, is_purchased: currentStatus } : b)));
    }
  }, []);

  const toggleWant = useCallback(async (asin, currentStatus) => {
    const nextStatus = currentStatus === 1 ? 0 : 1;
    // 楽観的更新
    setBooks((prev) => prev.map((b) => (b.asin === asin ? { ...b, is_wanted: nextStatus } : b)));
    try {
      const res = await fetch(`/api/want?asin=${encodeURIComponent(asin)}&status=${nextStatus}`, {
        method: 'POST',
      });
      const data = await res.json();
      if (!data.ok) {
        // ロールバック
        setBooks((prev) => prev.map((b) => (b.asin === asin ? { ...b, is_wanted: currentStatus } : b)));
      }
    } catch {
      setBooks((prev) => prev.map((b) => (b.asin === asin ? { ...b, is_wanted: currentStatus } : b)));
    }
  }, []);

  const toggleFilter = useCallback((mode) => {
    setFilterMode((prev) => (prev === mode ? 'all' : mode));
  }, []);

  const processedBooks = (() => {
    let result = [...books];

    if (filterMode === 'campaign') {
      result = result.filter((b) => b.is_unlimited === 0 && b.campaign_text);
    } else if (filterMode === 'unlimited') {
      result = result.filter((b) => b.is_unlimited === 1);
    } else if (filterMode === 'purchased') {
      result = result.filter((b) => b.is_purchased === 1);
    } else if (filterMode === 'unpurchased') {
      result = result.filter((b) => b.is_purchased === 0);
    } else if (filterMode === 'wanted') {
      result = result.filter((b) => b.is_wanted === 1);
    }

    if (sortMode === 'discount') {
      result.sort((a, b) => {
        const dA = a.sell_price ? a.point_value / a.sell_price : 0;
        const dB = b.sell_price ? b.point_value / b.sell_price : 0;
        return dB - dA;
      });
    } else if (sortMode === 'updated') {
      result.sort((a, b) => (b.timestamp || '').localeCompare(a.timestamp || ''));
    } else if (sortMode === 'unlimited') {
      result.sort((a, b) => (b.is_unlimited || 0) - (a.is_unlimited || 0));
    } else if (sortMode === 'price') {
      result.sort((a, b) => (a.actual_price || 0) - (b.actual_price || 0));
    }

    return result;
  })();

  return {
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
    toggleWant,
  };
}
