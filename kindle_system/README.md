# Kindle System

Kindle for PC のキャッシュから蔵書情報を抽出し、価格・キャンペーン情報をクロールして
読書メーターの「読みたい本」リストと同期し、結果を静的サイトとして公開する CLI ツール群。

常駐サーバー・ブラウザ操作の UI は無く、すべて `run.py` / `report.py` のコマンド実行で完結する。

## セットアップ

```
pip install -r requirements.txt
playwright install
copy .env.example .env
```

`.env.example` を参考に、必要な環境変数を `.env` に設定する。

- `KINDLE_XML_PATH`: 任意設定。Kindle for PC のキャッシュファイルのパスが標準と異なる場合のみ設定する。
- `PUBLIC_SITE_DIR` / `PUBLIC_SITE_URL`: 公開機能（`run.py sync` 内の `publish()`）を使う場合は必須。

## 使い方

### クロール → 読書メーター同期 → レポート生成 → 公開（一括実行）

```
python run.py sync [--workers N] [--limit N] [--start N] [--target kindle|bookmeter|both]
```

- `--workers`: 並列ブラウザ数（デフォルト 1、1〜5 にクランプされる）
- `--limit`: 処理する最大件数
- `--start`: 開始するインデックス番号
- `--target`: 実行対象（デフォルト `both`）
  - `kindle`: Kindleの「読みたい本」クロールのみ実行する
  - `bookmeter`: 読書メーターの「読みたい本」同期のみ実行する
  - `both`: 両方を順に実行する（従来の `run.py sync` と同じ挙動）

### 「欲しい本」フラグの更新

```
python run.py want <asin> --on
python run.py want <asin> --off
```

### 購入済みフラグの更新

```
python run.py purchase <asin> --on
python run.py purchase <asin> --off
```

### 静的レポートの生成のみ実行（公開はしない）

`run.py sync` は内部でレポート生成と公開（git commit・push）の両方を行うが、
`report.py` は単体でも実行できる。ただし **`report.py` 単体では公開は行われない**
（`PUBLIC_SITE_DIR` 直下に `wishlist.json` を書き出すだけで git 操作はしない）。
クロールをやり直さずに公開だけをやり直す CLI コマンドは現状無いため、
再公開が必要な場合は `python run.py sync` を実行する。

```
python report.py
```

蔵書一覧（読みたい本 / 購入済み本 / 全部）を `PUBLIC_SITE_DIR` 直下の
`wishlist.json`（データだけ。形式 `kindle-wishlist` v1）として書き出す。

画面は持たない。欲しい本の一覧は book-highlights アプリの本タブ「欲しい本」
（https://nihi566.github.io/book-highlights/#/wishlist）が同じオリジンからこの `wishlist.json` を読んで表示する。
見た目・操作を変えるときは book-highlights を直す。公開リポジトリの `index.html` はその画面へ移動する
静的ページで、`report.py` / `run.py sync` は触らない。

### 「見た」・★評価とローカル LLM のおすすめ

以下の「公開ページ」は、book-highlights アプリの欲しい本の画面を指す（タグの保存先・書き出しファイルの形式は旧画面と同じ）。

公開ページのカードでは、タグ（読みたい / 読みたくない / 購入済み / **見た**）に加えて、
「見た」を選んだ本に **★1〜5 の評価** を付けられる（同じ★をもう一度押すと取り消し）。
カード上部の「マンガ / 本」ボタンで種別を切り替えられ、ページ上部の「種別」で
マンガだけ・本だけに絞り込める。種別はタイトルのレーベル表記（「(ジャンプコミックスDIGITAL)」
「(モーニングKC)」等）から自動で判定し、表記の無いマンガはボタンで切り替える。

付けた内容はまずブラウザに保存される。これを DB に蓄積し、ローカル LLM におすすめを出してもらう手順:

1. 公開ページの「見た・評価を書き出す」で `kindle-marks-YYYYMMDD-HHMM.json` を保存する
   （スマホで書き出した場合はファイルを PC に送る）
2. PC で取り込む（ファイルを省略すると `MARKS_DOWNLOAD_DIR` か `~/Downloads` の最新の書き出しファイルを使う）

   ```
   python run.py import-marks [<書き出したファイル>] [--publish]
   ```

   `--publish` を付けると公開ページも作り直す。種別の切り替えは公開ページに反映されるが、
   「見た」・★評価・読みたくない等のタグは読書記録なので、既定では公開ページに載せない
   （DB とローカル LLM だけで使う）。`.env` で `PUBLISH_MARKS=1` にすると公開ページにも載り、
   別の端末でも同じ状態が初期表示される（公開の GitHub Pages に読書記録が載る点に注意）。
3. ローカル LLM を用意する（例: [Ollama](https://ollama.com/) を入れて `ollama pull qwen2.5:7b`）
4. おすすめを出す（マンガと本は別々に問い合わせる）

   ```
   python run.py recommend [--kind manga|book|all] [--count N] [--new N] [--model NAME] [--dry-run]
   ```

   - `--kind`: `manga`（マンガ）/ `book`（本）/ `all`（両方を別々に。デフォルト）
   - `--count`: 登録済みで未読の本から選ぶ件数（デフォルト 5）。候補に無い ASIN は採用しない
   - `--new`: 登録済み以外から挙げる件数（デフォルト 3、`0` で出さない）。LLM の知識によるので実在は要確認
   - `--model`: 使うモデル（省略時は `LOCAL_LLM_MODEL`、それも無ければ入っている最初のモデル）
   - `--dry-run`: LLM に送るプロンプトを表示するだけ

   接続先は `.env` の `LOCAL_LLM_URL`（デフォルト `http://localhost:11434` = Ollama）。
   LM Studio / llama.cpp server 等の OpenAI 互換 API を使う場合は `LOCAL_LLM_API=openai` にする。

### クロールのみ実行（読書メーター同期・公開を含まない）

```
python main.py
python main.py --limit 3    (先頭から最大3件まで処理)
python main.py --test       (ダミーXMLを用いてテスト実行)
```

## テスト

```
python3 -m unittest discover -s test
```
