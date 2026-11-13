# Snakes & Ladders Commentator

実物の Snakes & Ladders と連動し、カメラで認識したサイコロの出目をもとにゲーム状態を管理し、LLM が実況コメントを生成・表示・読み上げるシステムです。

現在はゲームエンジン、SQLite永続化、履歴の訂正・Undo/Redo・復元、手動入力API、管理者・プレイヤー画面が動作する段階です。既存のサイコロ認識プロトタイプは `dice_recognize/` にあります。本体へ移植して検証が終わるまでは参照用として残し、移植後に削除します。

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

AI間の引き継ぎ文書 `TOCLAUDE.md` と `TOCODEX.md` は、レビューと対応の経緯としてGit管理対象にします。

```bash
# 初回セットアップ
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
npm install

# ターミナル1: API
.venv/bin/uvicorn commentator.main:create_production_app --factory \
  --app-dir apps/server/src --reload --no-access-log

# ターミナル2: Web UI
npm run dev:web
```

- 管理者画面: `http://127.0.0.1:5173/`
- プレイヤー画面: `http://127.0.0.1:5173/player`
- APIドキュメント: `http://127.0.0.1:8000/docs`

ゲーム状態は既定で `var/commentator.sqlite3` に保存されます。`COMMENTATOR_DB_PATH` で保存先を変更できます。ゲーム・操作イベント・ターン・スナップショットは同じトランザクションで保存され、保存終了したゲームは管理者画面の一覧から再開できます。

プロセス起動時に進行中のまま残ったゲームは、異常終了からの復旧対象として自動的に一時停止します。管理者画面で実物の盤面と位置を確認してから再開してください。プレイヤー画面へ表示する「現在のゲーム」は明示的に保存され、古いゲームを編集しただけでは切り替わりません。

異常終了による一時停止もUndo履歴に「異常終了による一時停止」として表示されます。盤面を確認したうえで、通常はUndoではなく「再開」を選んでください。

すべての変更APIは `expectedStateVersion` を必須とし、現在値と異なる要求には409を返します。画面の操作中は送信ボタンを無効にします。現在のプロセス内排他制御を前提とするため、APIは1ワーカーで起動してください。

プレイヤー情報のゴール可能な出目は `winningRolls` として返します。ハシゴで100へ到達する出目も含みます（75マスでは `[5]`）。

品質チェックは次のコマンドで実行します。

```bash
.venv/bin/ruff check apps/server
.venv/bin/mypy
.venv/bin/pytest
npm run check:web
npm run build:web
```

この5項目はGitHub ActionsでもPull Requestと`main`へのpush時に実行します。`npm audit`は外部データの更新だけで結果が変わるため必須CIには含めず、定期または手動で実行します。

サーバーログはstdoutと既定の `var/log/commentator.jsonl` に1行1 JSONで出力します。保存方法は `COMMENTATOR_LOG_RETENTION=rotate|permanent` で選択できます。`permanent`は自動削除されないため、ディスク容量と研究データの管理に注意してください。詳細は [ログ運用](docs/operations/logging.md) を参照してください。

## 確定したゲーム進行方針

- プレイヤー人数のデフォルトは2人
- プレイヤー人数は2〜6人。同じゲーム内で色は重複させない
- 参加者がN人の場合、N−1人がゴールした時点で全順位を確定して終了する
- 残った1人は最下位として順位確定するが、ゴール済みにはしない
- ゴール済みプレイヤーは以降の手番から外す
- 管理者操作では、一時停止、保存して終了、ゲーム中止を区別する
- 保存して終了したゲームは後から再開できる
- 中止したゲームには正式な最終順位を付けない
- 同じマスの暫定順位は競技順位方式（例: 1位、1位、3位）とし、ゴール順位は到達順で一意にする
