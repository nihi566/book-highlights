// オフラインでも開けるようにするサービスワーカー。
// ネットワーク優先（更新をすぐ反映）で、つながらないときだけキャッシュを使う。
const CACHE = 'bh-v1';
const SHELL = [
  './',
  'index.html',
  'css/app.css',
  'manifest.webmanifest',
  'icons/icon.svg',
  'js/app.js',
  'js/db.js',
  'js/html.js',
  'js/services.js',
  'js/state.js',
  'js/ui.js',
  'js/views/library.js',
  'js/views/knowledge.js',
  'js/views/settings.js',
  'js/views/wishlist.js',
  'core/model.js',
  'core/wishlist.js',
  'core/text.js',
  'core/zip.js',
  'core/obsidian.js',
  'core/sample.js',
  'core/parsers/index.js',
  'core/parsers/blocks.js',
  'core/parsers/kindle-clippings.js',
  'core/parsers/kindle-notebook.js',
  'core/parsers/playbooks.js',
  'core/analysis/llm.js',
  'core/analysis/pipeline.js',
  'core/analysis/prompts.js',
  'core/analysis/recommend.js',
  'core/analysis/vectors.js',
  'bookmarklet/kindle-notebook.js',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  // 自分のオリジンの GET だけ扱う（API・LLM・Google Books は素通し）
  if (e.request.method !== 'GET' || url.origin !== location.origin || url.pathname.includes('/api/') || url.pathname.includes('/llm/')) return;
  e.respondWith(
    fetch(e.request)
      .then((res) => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
        }
        return res;
      })
      .catch(() => caches.match(e.request, { ignoreSearch: true }).then((r) => r || caches.match('index.html'))),
  );
});
