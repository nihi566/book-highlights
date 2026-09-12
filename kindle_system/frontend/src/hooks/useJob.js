import { useState, useRef, useCallback } from 'react';

/**
 * クロールジョブの実行・SSEログ受信を管理するカスタムフック
 */
export function useJob(onJobDone) {
  const [running, setRunning] = useState(false);
  const [sseLogs, setSseLogs] = useState([]);
  const sseRef = useRef(null);

  const startSSE = useCallback(() => {
    if (sseRef.current) sseRef.current.close();

    // SSE は同一オリジンの相対パスで接続する。
    // 本番は server.py が frontend/dist を同一オリジンで配信し、開発は vite の /api proxy を通る。
    // VITE_CRAWLER_URL は別オリジンにサーバーを置く場合の任意の上書き（既定では未設定）。
    const crawlerBase = import.meta.env.VITE_CRAWLER_URL || '';
    const sse = new EventSource(`${crawlerBase}/api/events`);
    sseRef.current = sse;

    sse.onmessage = (e) => {
      setSseLogs((prev) => [...prev, e.data]);
    };

    sse.addEventListener('done', () => {
      setSseLogs((prev) => [...prev, '完了しました。データを再読み込みしています...']);
      setRunning(false);
      sse.close();
      sseRef.current = null;
      if (onJobDone) onJobDone();
    });

    sse.onerror = () => {
      setRunning(false);
      if (sseRef.current) {
        sseRef.current.close();
        sseRef.current = null;
      }
    };
  }, [onJobDone]);

  const checkStatus = useCallback(async () => {
    try {
      const res = await fetch('/api/status');
      if (res.ok) {
        const data = await res.json();
        if (data.running) {
          setRunning(true);
          startSSE();
        }
      }
    } catch {
      // 起動直後などサーバー未接続の場合は無視
    }
  }, [startSSE]);

  const startJob = useCallback(async (startIndex) => {
    const cleanIndex = String(startIndex).trim();
    setSseLogs([]);
    setRunning(true);
    try {
      const res = await fetch(`/api/run?start=${encodeURIComponent(cleanIndex)}`, {
        method: 'POST',
      });
      const data = await res.json();
      if (data.ok) {
        startSSE();
      } else {
        setRunning(false);
        return { error: data.message || 'ジョブの開始に失敗しました。' };
      }
    } catch {
      setRunning(false);
      return { error: 'サーバーに接続できません。' };
    }
    return {};
  }, [startSSE]);

  const stopJob = useCallback(async () => {
    try {
      const res = await fetch('/api/stop', { method: 'POST' });
      const data = await res.json();
      if (!data.ok) {
        return { error: data.message };
      }
    } catch {
      return { error: 'サーバー通信に失敗しました。' };
    }
    return {};
  }, []);

  const closeSSE = useCallback(() => {
    if (sseRef.current) sseRef.current.close();
  }, []);

  return { running, sseLogs, checkStatus, startJob, stopJob, closeSSE };
}
