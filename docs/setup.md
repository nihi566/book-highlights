# セットアップ詳細

## 0. 必要なもの

- PC: Node.js 20 以上、Obsidian、ローカル LLM（[Ollama](https://ollama.com) か LM Studio など OpenAI 互換 API を持つもの）
- スマホ: ブラウザ（Safari / Chrome）。PC の AI を使うなら [Tailscale](https://tailscale.com)

## 1. Web アプリを GitHub Pages で公開する

1. このリポジトリの **Settings → Pages → Build and deployment → Source** を「**GitHub Actions**」にする
2. `main` ブランチに push すると `.github/workflows/pages.yml` がテストして `web/` を公開する
3. `https://<ユーザー名>.github.io/book-highlights/` を開き、スマホではホーム画面に追加する

公開されるのはアプリのプログラムだけです。ハイライトは各端末のブラウザ（IndexedDB）と PC の `data/` にしか保存されません。

非公開（private）リポジトリで GitHub Pages を使うには有料プラン（GitHub Pro など）が必要です。無料プランの場合は、リポジトリを公開にする（ハイライトのデータはリポジトリに含まれないので公開しても中身は漏れません）か、GitHub Pages を使わずに PC の `http://localhost:8787` と Tailscale の URL だけで使ってください（それでもすべての機能が使えます）。

## 2. PC: ローカル LLM

### Ollama

```sh
ollama pull qwen3.5:9b   # チャット。メモリが少なければ qwen3.5:4b、余裕があれば 27b
ollama pull bge-m3       # 埋め込み（多言語）。qwen3-embedding:0.6b なども可
```

- チャットモデルは日本語が得意なもの（Qwen 3.5 / 3.6、Gemma 4 など）を選びます。小さいモデルでも動くよう、1 回の依頼は小さく、出力は JSON スキーマで固定しています
- 思考（thinking）モデルは `reasoning_effort: "none"` で思考を切って呼びます（速く、JSON が崩れにくい）
- 埋め込みモデルは任意です。無い場合は文字 n-gram の TF-IDF で代用します（語彙が同じ点どうしはつながりますが、言い換えには弱くなります）

### LM Studio / llama.cpp server

```sh
node cli/bh.js config url http://localhost:1234   # LM Studio の既定ポート
```

## 3. PC: `bh` の設定とコンパニオンサーバ

```sh
node cli/bh.js config vault "/Users/me/Documents/MyVault"
node cli/bh.js config model qwen3.5:9b
node cli/bh.js config embed bge-m3
node cli/bh.js config origin https://<ユーザー名>.github.io   # GitHub Pages 版から接続する場合
node cli/bh.js serve
```

| コマンド | 内容 |
| --- | --- |
| `bh import <ファイル...> [--obsidian]` | 取り込み（`--obsidian` で続けて Vault に書き出し） |
| `bh obsidian [--dry-run]` | Vault に書き出し |
| `bh analyze [--no-recommend]` | 点→線→面→立体の分析とおすすめ（結果は Vault にも書き出し） |
| `bh recommend` | おすすめだけ選び直す |
| `bh serve [--port 8787] [--host 127.0.0.1]` | コンパニオンサーバ |
| `bh list` / `bh search <語>` | 一覧・検索 |
| `bh config` | 設定の表示（`data/config.json`） |

データは既定でリポジトリの `data/`（`.gitignore` 済み）に保存されます。`BH_DATA=/path` で変更できます。

常駐させたい場合は、macOS なら launchd、Windows ならタスク スケジューラ、Linux なら systemd のユーザーサービスで `node /path/to/cli/bh.js serve` を起動します。

## 4. スマホから PC につなぐ（Tailscale）

1. Tailscale の管理画面の **DNS** で MagicDNS をオンにし、**HTTPS Certificates** を有効にする
2. PC で:
   ```sh
   tailscale serve --bg 8787
   tailscale serve status   # https://<PC名>.<tailnet>.ts.net が表示される
   ```
3. スマホに Tailscale アプリを入れて同じアカウントでログイン
4. Web アプリの「設定 → AI」で「PC のコンパニオンサーバ」を選び、URL に `https://<PC名>.<tailnet>.ts.net` を入れて「接続を確認」

その URL をスマホのブラウザで直接開いても、同じ Web アプリが使えます（PC が配信）。

- tailnet の外からは見えません。`*.ts.net` からの接続はコンパニオンサーバが自動で許可します
- Chrome / Android では初回に「ローカルネットワークへのアクセス」の許可を求められることがあります。許可してください

### tailnet を使わない場合

`cloudflared tunnel --url http://localhost:8787` などでインターネットに公開する場合は、**必ずトークンを設定**してください。

```sh
node cli/bh.js config token <長いランダムな文字列>
node cli/bh.js config origin https://<ユーザー名>.github.io
```

Web アプリの「設定 → AI → トークン」に同じ文字列を入れます。

## 5. ブラウザから LLM に直接つなぐ（PC のみ・任意）

コンパニオンサーバを使わず、PC の Chrome / Edge / Firefox から Ollama を直接呼ぶこともできます。

1. Ollama に Web アプリのオリジンを許可する（スペースを入れない。パスは不要）
   - macOS: `launchctl setenv OLLAMA_ORIGINS "https://<ユーザー名>.github.io"` → Ollama を再起動
   - Windows: 環境変数 `OLLAMA_ORIGINS` に `https://<ユーザー名>.github.io` を追加 → Ollama を再起動
   - Linux: `systemctl edit ollama.service` で `Environment="OLLAMA_ORIGINS=https://<ユーザー名>.github.io"` → `systemctl daemon-reload && systemctl restart ollama`
2. Web アプリの「設定 → AI」で「このブラウザから LLM に直接」を選び、`http://localhost:11434` とモデル名を入れる
3. 初回にブラウザが「ローカルネットワーク（このデバイス）へのアクセス」の許可を求めたら許可する

**Safari は https のページから `http://localhost` に接続できません。** Mac の Safari では `http://localhost:8787`（コンパニオンサーバ）を開いてください。`https://*.github.io` のようなワイルドカードは、他人の GitHub Pages からも Ollama を呼べてしまうので避けてください。

## 6. Obsidian をスマホでも読む

分析結果は Vault の Markdown / Canvas になるので、Obsidian Sync・iCloud Drive・Git など、普段の同期方法でスマホの Obsidian からも読めます。Web アプリの「Obsidian に写す」で Vault 名を設定すると、本の画面に「Obsidian で開く」ボタンが出ます。

## うまくいかないとき

| 症状 | 対処 |
| --- | --- |
| 「PC に接続できません」 | `bh serve` が動いているか、URL（`http://localhost:8787` / `https://…ts.net`）が正しいか |
| 「このオリジンからの接続は許可されていません」 | `bh config origin https://<ユーザー名>.github.io` |
| 「LLM サーバに接続できません」 | Ollama が起動しているか（`ollama list`）、`bh config url` が正しいか |
| 「JSON 形式の応答を得られませんでした」 | モデルが小さすぎる可能性。大きめのモデルに変える |
| Play ブックスのファイルでハイライトが 0 件 | ドライブの「Play ブックスのメモ」のドキュメントか確認。.docx / .html / .md で保存する（.txt / .pdf は非対応） |
| ブックマークレットで「本が見つかりませんでした」 | read.amazon.co.jp/notebook にログインした状態で実行する |
