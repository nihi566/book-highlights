import React, { useEffect, useRef } from 'react';

const LINE_CLASSES = {
  book:  /^\[Worker-\d+\]\[\d+\/\d+\]/,
  ok:    /\[OK\]|\[Report\]/,
  error: /\[Error\]|\[タイムアウト\]|\[停止\]/,
  sleep: /\[Sleep\]/,
  step:  /^  \[\d\/\d\]|^  ->/,
};

function classifyLine(text) {
  if (LINE_CLASSES.book.test(text))  return 'log-book';
  if (LINE_CLASSES.ok.test(text))    return 'log-ok';
  if (LINE_CLASSES.error.test(text)) return 'log-error';
  if (LINE_CLASSES.sleep.test(text)) return 'log-sleep';
  if (LINE_CLASSES.step.test(text))  return 'log-step';
  return '';
}

export default function TerminalLog({ logs, visible }) {
  const terminalRef = useRef(null);

  // 新しいログが追加されたら自動スクロール
  useEffect(() => {
    if (terminalRef.current) {
      terminalRef.current.scrollTop = terminalRef.current.scrollHeight;
    }
  }, [logs]);

  if (!visible) return null;

  return (
    <div className="terminal-wrap">
      <div className="terminal-header">
        <span className="terminal-title">実行ログ</span>
        {logs.length > 0 && (
          <span className="term-counter">{logs.length} 行</span>
        )}
      </div>
      <div className="terminal" ref={terminalRef}>
        {logs.length === 0 ? (
          <p className="log-waiting">処理を開始しています...</p>
        ) : (
          logs.map((log, idx) => (
            <p key={idx} className={classifyLine(log)}>
              {log}
            </p>
          ))
        )}
      </div>
    </div>
  );
}
