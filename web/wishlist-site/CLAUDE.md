# CLAUDE.md

Claude Code が回答するときは日本語で回答すること。

## このリポジトリは欲しい本のデータ置き場（画面は持たない）

欲しい本の画面は book-highlights アプリ（nihi566/book-highlights の `web/`、
https://nihi566.github.io/book-highlights/#/wishlist）にある。同じオリジン（nihi566.github.io）なので、
アプリがこのリポジトリの `wishlist.json` を fetch して表示し、タグ（localStorage）も共有される。

| ファイル | 管理 |
|---|---|
| `wishlist.json` | **生成物（直接編集しない）**。`C:/dev/kindle_system/report.py`（nihi566/kindle_system）が書き出し、自動公開（`python run.py sync`）のたびに上書きされる。形式は `kindle-wishlist` v1 |
| `feed.xml` | **生成物（直接編集しない）**。欲しい本の値下がり・読み放題入りを知らせる Atom フィード。`report.py`（`build_feed`）が wishlist.json と一緒に書き出す |
| `index.html` | 旧 URL から book-highlights の欲しい本の画面へ移動するだけの静的ページ。このリポジトリで直接管理する（`report.py` は触らない） |
| `favicon.svg` | このリポジトリで直接管理する |

- 見た目・操作を変えるときは book-highlights の `web/js/views/wishlist.js` / `web/core/wishlist.js` を直す
- データの項目を変えるときは `kindle_system` の `report.py`（`build_wishlist`）と `test/test_report.py` を直して PR にし、
  マージ後に `python report.py`（または `python run.py sync`）で作り直す（`.env` の `PUBLIC_SITE_DIR` がこのリポジトリのローカルクローンを指す）。
  book-highlights 側の読み込み（`parseWishlist`）も同じ版に合わせる
