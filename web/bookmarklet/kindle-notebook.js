// Kindle ノートブック（https://read.amazon.co.jp/notebook）から全ての本のハイライトを集めるブックマークレット
// Web アプリの「取り込み」画面が、このファイルを javascript: URL にして配布する（__APP_URL__ はそのとき置き換わる）。
// 取得したデータは JSON ファイルとして保存するか、Web アプリに直接送る。Amazon 以外には何も送らない。
// 読み取りのセレクタは extension/offscreen.js（自動取り込みの拡張機能）と同じ。Amazon の画面が変わったら両方を直す。
(async () => {
  const APP_URL = '__APP_URL__';
  if (!/^read\.amazon\./.test(location.hostname) || !location.pathname.startsWith('/notebook')) {
    alert('Kindle のノートブック（https://read.amazon.co.jp/notebook）を開いて、ログインしてから実行してください。');
    return;
  }
  if (window.__bhRunning) return;
  window.__bhRunning = true;

  const box = document.createElement('div');
  box.style.cssText = 'position:fixed;z-index:2147483647;right:16px;bottom:16px;width:320px;max-width:calc(100vw - 32px);background:#fff;color:#222;border-radius:12px;box-shadow:0 8px 32px rgba(0,0,0,.25);padding:16px;font:14px/1.6 system-ui,sans-serif';
  const title = document.createElement('div');
  title.style.cssText = 'font-weight:700;margin-bottom:6px';
  title.textContent = 'ハイライトを集めています…';
  const status = document.createElement('div');
  const actions = document.createElement('div');
  actions.style.cssText = 'display:flex;gap:8px;margin-top:12px;flex-wrap:wrap';
  box.append(title, status, actions);
  document.body.append(box);
  let cancelled = false;
  const button = (label, onClick, primary) => {
    const b = document.createElement('button');
    b.textContent = label;
    b.style.cssText = `padding:8px 12px;border-radius:8px;border:1px solid #888;cursor:pointer;font:inherit;${primary ? 'background:#1f6f5c;color:#fff;border-color:#1f6f5c' : 'background:#fff;color:#222'}`;
    b.onclick = onClick;
    actions.append(b);
    return b;
  };
  const cancelBtn = button('中止', () => (cancelled = true));
  const close = () => {
    box.remove();
    window.__bhRunning = false;
  };

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const parse = (html) => new DOMParser().parseFromString(html, 'text/html');
  const get = async (url) => {
    const r = await fetch(url, { credentials: 'include' });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return parse(await r.text());
  };
  const txt = (el) => (el?.textContent || '').replace(/\s+/g, ' ').trim();
  const val = (root, sel) => root.querySelector(sel)?.getAttribute('value') || '';

  try {
    // 1. 本の一覧（ページ送りあり）
    const books = [];
    const seen = new Set();
    const collect = (d) =>
      d.querySelectorAll('.kp-notebook-library-each-book').forEach((el) => {
        if (!el.id || seen.has(el.id)) return;
        seen.add(el.id);
        books.push({
          asin: el.id,
          title: txt(el.querySelector('h2.kp-notebook-searchable') || el.querySelector('h2')),
          author: txt(el.querySelector('p.kp-notebook-searchable')).replace(/^[^:：]{1,10}[:：]\s*/, ''),
          lastAnnotated: val(el, '[id^="kp-notebook-annotated-date"]'),
          highlights: [],
        });
      });
    collect(document);
    let token = val(document, '.kp-notebook-library-next-page-start');
    for (let i = 0; token && i < 100 && !cancelled; i++) {
      status.textContent = `本の一覧を読み込み中（${books.length} 冊）`;
      const d = await get(`/notebook?library=list&token=${encodeURIComponent(token)}`);
      collect(d);
      token = val(d, '.kp-notebook-library-next-page-start');
    }
    if (!books.length) throw new Error('本が見つかりませんでした。ログインしているか確認してください。');

    // 2. 本ごとのハイライト（ページ送りあり）
    let total = 0;
    for (let i = 0; i < books.length && !cancelled; i++) {
      const b = books[i];
      status.textContent = `${i + 1}/${books.length} 冊目: ${b.title}（${total} 件）`;
      let next = '';
      let state = '';
      for (let page = 0; page < 200 && !cancelled; page++) {
        const d = await get(`/notebook?asin=${encodeURIComponent(b.asin)}&contentLimitState=${encodeURIComponent(state)}&token=${encodeURIComponent(next)}`);
        d.querySelectorAll('.a-row.a-spacing-base').forEach((row) => {
          const text = txt(row.querySelector('#highlight'));
          const noteEl = row.querySelector('#note');
          noteEl?.querySelectorAll('br').forEach((br) => br.replaceWith('\n'));
          const note = (noteEl?.textContent || '').trim();
          if (!text && !note) return;
          const header = txt(row.querySelector('#annotationHighlightHeader')) || txt(row.querySelector('#annotationNoteHeader'));
          const pageMatch = header.match(/(?:Page|ページ)\s*[:：]?\s*([0-9ivxlcdm]+)/i);
          const color = (row.querySelector('.kp-notebook-highlight')?.className || '').match(/kp-notebook-highlight-(\w+)/);
          b.highlights.push({ text, note, location: val(row, '#kp-annotation-location'), page: pageMatch ? pageMatch[1] : '', color: color ? color[1] : '' });
          total++;
        });
        next = val(d, '.kp-notebook-annotations-next-page-start');
        state = val(d, '.kp-notebook-content-limit-state');
        if (!next) break;
        await sleep(200);
      }
      await sleep(200);
    }

    // 3. 結果を渡す
    const data = { format: 'book-highlights/kindle-notebook', version: 1, exportedAt: new Date().toISOString(), source: location.origin, books: books.filter((b) => b.highlights.length) };
    title.textContent = cancelled ? '中止しました（途中までのデータがあります）' : '集め終わりました';
    status.textContent = `本 ${data.books.length} 冊 / ハイライト ${total} 件`;
    cancelBtn.remove();
    const json = JSON.stringify(data);
    button(
      'ファイルに保存',
      () => {
        const a = document.createElement('a');
        a.href = URL.createObjectURL(new Blob([json], { type: 'application/json' }));
        a.download = `kindle-highlights-${data.exportedAt.slice(0, 10)}.json`;
        document.body.append(a);
        a.click();
        a.remove();
      },
      !APP_URL.startsWith('http'),
    );
    if (APP_URL.startsWith('http')) {
      button(
        'アプリに送る',
        () => {
          const appOrigin = new URL(APP_URL).origin;
          const w = window.open(`${APP_URL}#/import?from=bookmarklet`, '_blank');
          if (!w) return alert('ポップアップがブロックされました。「ファイルに保存」を使ってください。');
          const onMessage = (e) => {
            if (e.origin !== appOrigin || e.data?.type !== 'bh-ready') return;
            w.postMessage({ type: 'bh-import', data }, appOrigin);
            window.removeEventListener('message', onMessage);
            status.textContent = 'アプリに送りました';
          };
          window.addEventListener('message', onMessage);
        },
        true,
      );
    }
    button('閉じる', close);
  } catch (e) {
    title.textContent = 'エラー';
    status.textContent = e.message;
    cancelBtn.remove();
    button('閉じる', close);
  }
})();
