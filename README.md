# Snakes & Ladders Commentator

実物の Snakes & Ladders と連動し、カメラで認識したサイコロの出目をもとにゲーム状態を管理し、LLM が実況コメントを生成・表示・読み上げるシステムです。

現在はゲームエンジン、手動入力API、管理者・プレイヤー画面の初期実装が動作する段階です。既存のサイコロ認識プロトタイプは `dice_recognize/` にあります。本体へ移植して検証が終わるまでは参照用として残し、移植後に削除します。

## ドキュメント

- [全体構成・設計方針](docs/ARCHITECTURE.md)
- [開発 ToDo](TODO.md)

## 初期方針

- 対象環境: macOS上の単一PC
- 画面構成: 管理者画面とプレイヤー画面を別ブラウザウィンドウで表示
- バックエンド: Python / FastAPI
- 管理者・プレイヤー UI: TypeScript / React
- 永続化: SQLite（抽象化し、必要なら PostgreSQL に移行可能にする）
- リアルタイム通知: WebSocket
- サイコロ認識: USBカメラ / OpenCV
- LLM: Claude API / OpenAI API を共通インターフェースで切り替え
- 読み上げ: macOS標準TTS（音声、速度、音量などを設定可能にする）
- 録音: DJI Mic Mini受信機から2チャンネル音声のみを収録

この技術構成は暫定です。実装開始時に `docs/decisions/` の ADR で確定事項を記録します。

## 現在の起動方法

開発環境はPython 3.13系（`.python-version`）とNode.js 22系（`.nvmrc`）を使用します。nvmを利用する場合はセットアップ前に `nvm install && nvm use` を実行してください。

`.npmrc` の `engine-strict=true` により、指定外のNodeバージョンでは `npm install` がエラーになります。先にNode 22系へ切り替えてください。

AI間の引き継ぎ文書 `TOCLAUDE.md` と `TOCHATGPT.md` は、レビューと対応の経緯としてGit管理対象にします。

```bash
# 初回セットアップ
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
npm install

# ターミナル1: API
.venv/bin/uvicorn commentator.main:app --app-dir apps/server/src --reload

# ターミナル2: Web UI
npm run dev:web
```

- 管理者画面: `http://127.0.0.1:5173/`
- プレイヤー画面: `http://127.0.0.1:5173/player`
- APIドキュメント: `http://127.0.0.1:8000/docs`

現段階の状態はメモリ上にのみ保存され、プロセスを終了すると消えます。DB保存、Undo/Redo、WebSocket配信は次の実装段階です。

手動出目APIは `expectedTurnNumber` を必須とし、現在のターン番号と異なる要求には409を返します。画面の操作中は送信ボタンを無効にします。現在の排他制御は単一プロセス内で有効なため、APIは1ワーカーで起動してください。

プレイヤー情報のゴール可能な出目は `winningRolls` として返します。ハシゴで100へ到達する出目も含みます（75マスでは `[5]`）。

品質チェックは次のコマンドで実行します。

```bash
.venv/bin/ruff check apps/server
.venv/bin/mypy
.venv/bin/pytest
npm run check:web
npm run build:web
npm audit
```

## 確定したゲーム進行方針

- プレイヤー人数のデフォルトは2人
- 参加者がN人の場合、N−1人がゴールした時点で全順位を確定して終了する
- 残った1人は最下位として順位確定するが、ゴール済みにはしない
- ゴール済みプレイヤーは以降の手番から外す
- 管理者操作では、一時停止、保存して終了、ゲーム中止を区別する
- 保存して終了したゲームは後から再開できる
- 中止したゲームには正式な最終順位を付けない
