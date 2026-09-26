# CLAUDE.md

Claude Code が回答するときは日本語で回答すること。

## index.html は生成物（直接編集しない）

`index.html` は `C:/dev/kindle_system/report.py`（リポジトリ nihi566/kindle_system）が生成し、
自動公開（`python run.py sync`）のたびに上書きされる。**このリポジトリで index.html を直接編集すると、
次の自動公開で変更が消える。**

見た目・操作を変えるときの手順:

1. `kindle_system` の `report.py`（`_PAGE_STYLE` / `_PAGE_HEADER_HTML` / `_PAGE_SCRIPT` と行・セクションの組み立て）を変更し、
   `test/test_report.py` を追従させて PR にする
2. マージ後、`kindle_system` で `python report.py`（または `python run.py sync`）を実行して
   index.html を作り直す（`.env` の `PUBLIC_SITE_DIR` がこのリポジトリのローカルクローンを指す）

`favicon.svg` など index.html 以外のファイルは生成されないので、このリポジトリで直接管理する。
