# Claude向け実装・レビュー報告

## Phase 0レビュー必須修正への対応報告（2026-11-13）

`TOCODEX.md` の「Phase 0 残件とPhase 3着手前修正の確認」にある必須2件を修正した。Phase 3には着手していない。

### 1. ゲーム完了後の後続操作の無効化

- 出目訂正の再構築中にゲームが `COMPLETED` になった場合、それ以降のROLLと非ROLL操作をすべて `game_completed` として無効化するようにした。
- 3人以上のゲームで、訂正により先にゴールしたプレイヤーへの後続位置訂正は `player_already_finished` として無効化するようにした。
- 既存の `invalidatedTurns` とは別に `invalidatedOperations` をAPIのdry run、確認必須エラー、CORRECTION監査payloadへ追加した。
- 管理画面の確認ダイアログへ、一時停止、再開、保存終了、中止、位置訂正も表示するようにした。
- 2人ゲームで `pause/resume` または `suspend/resume` を挟む再現ケースを追加し、訂正成功、後続操作の無効化、Undoによる元の有効履歴の復元を確認した。
- ゴール済みプレイヤーへの後続位置訂正を無効化する3人ゲームのテストも追加した。

### 2. import時の本番ログ・DB生成の廃止

- モジュール末尾の本番用 `app` 生成を削除し、uvicornのfactoryから呼ぶ `create_production_app()` を追加した。
- `import commentator.main` だけではSQLite Repositoryもログも作成・設定されない。
- READMEの起動コマンドを `commentator.main:create_production_app --factory` に変更した。
- uvicornの重複するプレーンテキストアクセスログを止めるため、起動コマンドへ `--no-access-log` を追加した。
- subprocessでmainをimportし、指定した本番DB・ログパスが作られないテストを追加した。別の一時ディレクトリでfactoryを呼ぶ確認では、DBとJSONLが作られることも確認した。

### 同時に対応した軽微な指摘

- `httpx` と `httpcore` のログレベルをWARNINGへ上げた。
- rotateの日数超過ファイル削除はサーバー起動時のみであることをログ運用文書へ明記した。
- 500を含むエラーレスポンスすべてへ `X-Request-ID` を付けるよう、共通エラー応答側で設定した。

### 検証結果

| 検証 | 結果 |
|---|---|
| `.venv/bin/ruff check apps/server` | 成功 |
| `.venv/bin/mypy` | 23ファイル、エラーなし |
| `.venv/bin/pytest -q` | 58件成功 |
| `npm run check:web` | 成功 |
| `npm run build:web` | 成功 |
| `git diff --check` | 成功 |
| GitHub Actions | 未実行（pushしていない） |

### ユーザー確認待ち

- `var/log/commentator.jsonl` は既存テスト由来のログを含む可能性がある。レビューの指示どおり、ユーザーの許可なしには削除していない。
- GitHubへのpushも行っていない。ユーザーの明示確認後にのみ実施する。

---

## Phase 0残件の実装報告（2026-11-13）

`TOCODEX.md` の「Phase 0 残件の対応方針」A〜Eを実装した。Phase 3の機能には着手していない。

### CI

- `.github/workflows/quality.yml` を追加した。
- Pull Requestと`main`へのpushを対象にし、同じrefの古い実行をcancelする。
- Ubuntu、Python 3.13、Node.js 22で、Ruff、mypy、pytest、Web型検査、Vite本番ビルドを必須チェックにした。
- `npm audit`、実機、外部APIを使う検査は必須CIへ含めていない。
- GitHubへのpushは行っていないため、GitHub Actions上での実行結果はまだない。ローカルでは同じ5チェックに成功した。

### ID・UTC・APIエラー

- ADR 0003に、エンティティUUID v4、決定的履歴UUID v5、サーバー生成request ID、UTC、単調増加時計、APIエラー、ログ規約を記録した。
- 全HTTP要求へUUID v4のrequest IDを付け、`X-Request-ID`、エラー本文、構造化ログで共有するmiddlewareを追加した。
- APIエラーを `detail: { code, message, context, requestId }` に統一した。入力検証エラーもリクエスト本文を含めず同じ形式で返す。
- Webへ `ApiRequestError` を追加し、`code`、`context`、`requestId`を保持する。`state_version_conflict` はcodeで分岐して最新状態の再読込を案内する。
- SQLiteの `updated_at` をUTCの `+00:00` 形式へ変更した。

### 構造化ログと保存方式

- `commentator/infrastructure/logging/` にJSON Formatter、環境設定、保持処理、logging初期化を追加した。
- stdoutと `var/log/commentator.jsonl` へ1行1 JSONで出力する。
- `COMMENTATOR_LOG_RETENTION=rotate|permanent` を実装し、既定を`rotate`にした。
- rotateは既定30日、10MB、5世代で、日数・サイズ・世代数を環境変数から変更できる。permanentは日単位で分割し、自動削除しない。
- 起動時に `logging_configured` イベントで実際の保持設定を記録する。不正な保持方式や数値は明確なValueErrorで起動を中止する。
- Formatterは許可した構造化フィールドだけを出し、Authorizationなど任意のextraを出力しない。APIログも本文を記録しない。
- `.env.example` と `docs/operations/logging.md` に設定、ディスク使用量、研究データ、個人情報の注意を記載した。

### ゲーム仕様

- プレイヤー人数を2〜6人に制限した。
- 色は大文字・小文字を区別せず、同じゲーム内の重複を422で拒否する。APIとドメインの両方で検証する。
- 同じマスの暫定順位は競技順位方式（`1, 1, 3`）、ゴール順位は到達順で一意とする規則をARCHITECTUREとREADMEへ記録した。
- ユーザースタディの主対象である2人構成を既定のまま維持した。

### 文書・TODO

- `docs/decisions/0003-observability-and-api-conventions.md`、`docs/api/errors.md`、`docs/operations/logging.md`を追加した。
- `docs/decisions/template.md` と `docs/api/README.md` を追加し、ADRとAPI契約の更新手順を整備した。
- 包括的だった「残っている仕様項目」を削除し、Phase 6のコメント訂正、Phase 7の録音、Phase 8のTTSへ具体的な決定項目を移した。
- Phase 5へログ保持方式を管理画面から変更するP1項目を追加した。
- Phase 0のCI、規約、仕様項目と、ADR/API更新手順を完了にした。

### 検証結果

| 検証 | 結果 |
|---|---|
| `.venv/bin/ruff check apps/server` | 成功 |
| `.venv/bin/mypy` | 23ファイル、エラーなし |
| `.venv/bin/pytest -q` | 53件成功 |
| `npm run check:web` | 成功 |
| `npm run build:web` | 成功 |
| GitHub Actions | 未実行（pushしていない） |

新規テストでは、JSONログ、秘密フィールド非出力、rotate/permanent、不正設定、request IDのヘッダー・エラー・ログ相関、2〜6人と色重複、SQLite UTC時刻を確認した。

---

## Phase 0残件の対応案（提案時点、2026-11-13）

ユーザーから、TODOのPhase 0〜2に未チェック項目が残っていることについて確認があった。以下は承認前に提示した案であり、確定・実装結果は上の報告を正とする。

### 1. CI設定

推奨案はGitHub Actionsへ `.github/workflows/quality.yml` を追加すること。

- 対象: `pull_request` と `main` へのpush。連続pushでは古い実行をcancelする。
- 実行環境: `ubuntu-latest`、Python 3.13、Node.js 22、npm cache。
- セットアップ: `pip install -e '.[dev]'` と `npm ci`。
- 必須チェック:
  - `.venv` に依存しない `ruff check apps/server`
  - `mypy`
  - `pytest -q`
  - `npm run check:web`
  - `npm run build:web`
- カメラ、マイク、macOS TTS、外部LLM APIを使うテストはCIへ含めない。Phase 3以降も保存画像・フェイクで動くテストだけを通常CIへ追加する。
- macOS実機固有の確認は通常CIと分離し、将来 `workflow_dispatch` のmacOS smoke testまたは運用チェックリストにする。
- `npm audit` は外部データ更新だけで結果が変わるため、必須PRチェックではなく定期実行または手動確認にする。

完了条件は、クリーンなcheckoutから上記5チェックが成功し、READMEにCIと同じローカルコマンドが記載されていること。

### 2. 共通ID・時刻・エラー・構造化ログ規約

規約をADRとAPI文書に残し、既存コードも同じ規約へ揃えることを提案する。

#### ID

- game、player、requestなど新規エンティティはUUID v4の小文字ハイフン形式。
- turnとoperation eventは履歴再生の決定性が必要なので、ADR 0001どおりUUID v5。
- APIではIDを文字列として扱い、空文字を拒否する。DB・ログ・APIで同じ値を使う。
- HTTP要求ごとに `requestId` を生成し、レスポンスの `X-Request-ID`、エラー、ログへ同じ値を出す。クライアントが妥当なIDを送った場合に引き継ぐかは、初期版では採用せずサーバー生成に統一する。

#### 時刻

- 永続化・API・ログはtimezone-awareなUTCに統一し、ISO 8601の `+00:00` 形式で保存する。
- 現在ローカルoffsetを使うSQLiteの `updated_at` も `datetime.now(UTC)` に変更する。
- UIだけがローカル時刻へ変換する。
- 経過時間の測定にはwall clockではなく単調増加時計を使う。
- ドメイン操作は引き続き `now` を注入できる形にし、テストを決定的にする。

#### APIエラー

- エラー本文を `detail: { code, message, context, requestId }` に統一する。
- `code` は機械判定用snake_case、`message` は日本語の表示用、`context` は安全な補足情報のみ。
- 主なHTTP分類:
  - 404: 対象が存在しない
  - 409: stateVersion競合、状態遷移不可、追加確認が必要
  - 422: リクエスト形式・値の検証失敗
  - 503: SQLiteなど一時的な保存失敗
- 現在の文字列だけの `detail` と辞書形式が混在しているため、例外クラスとFastAPI exception handlerを用意して一箇所で変換する。
- Webクライアントは `code` を優先して分岐し、`message` を表示する。

#### 構造化ログ

- Python標準 `logging` にJSON Formatterを用意し、stdoutへ1行1JSONで出力する。初期段階では外部ログサービスを導入しない。
- 共通フィールド: `timestamp`、`level`、`event`、`logger`、`requestId`、`gameId`、`stateVersion`、`turnId`、`durationMs`、`errorCode`。
- API middlewareで要求開始・完了・例外を記録し、ゲーム操作、異常終了検出、DB保存失敗もイベント名付きで記録する。
- APIキー、Authorization、音声本文、LLMプロンプト全文、カメラ画像、プレイヤーの読み方はログへ出さない。例外のpayloadも許可リスト方式にする。
- ローカルファイル保存が必要なら `var/log/` のローテーション済みJSONLとし、stdoutログを正とする。
- テストではJSONとしてparseできること、requestIdの相関、秘密値が含まれないことを確認する。

推奨する成果物:

- `docs/decisions/0003-observability-and-api-conventions.md`
- `docs/api/errors.md`
- `docs/operations/logging.md`
- `commentator/api/errors.py`
- `commentator/infrastructure/logging/`

### 3. 「残っている仕様項目」の分割

包括的な1項目のままでは完了判定できないため、ARCHITECTUREの残決定6件を次のように分けることを提案する。

#### Phase 3前に決める

1. プレイヤー人数は初期版で2〜8人。物理コマの識別性を優先し、1ゲーム内の色重複は禁止する。現在のAPI検証と将来の設定UIへ同じ制約を入れる。
2. 同じマスの暫定順位は現在実装済みの競技順位方式（例: `1, 1, 3`）に確定し、ゴール順位だけは到達順で一意にする。
3. ログの保存・個人情報規約は上記の構造化ログ規約で先に決める。提案値は、ローカルログを「30日または10MB×5世代の早い方」で削除し、秘密値・画像・音声・プロンプト全文を通常ログへ保存しないこと。

#### 各機能の着手前または実機検証時へ移す

4. コメント訂正表示はPhase 6前に決める。推奨は、DBには旧コメントを無効状態で残し、通常画面は最新の有効コメントへ置換する方式。
5. DJI Mic Miniの左右チャンネル割当と拡張子はPhase 7の実機検証項目へ移す。コードだけで先に確定しない。
6. macOS TTSの音声・速度・音量・高さの範囲はPhase 8の対象Macで列挙・試聴して決める。
7. 録音の保存期間と個人情報はPhase 7前に決める。推奨は自動クラウド送信なし、管理者が明示削除するまでローカル保持、録音中表示と保存先表示を必須にする方式。自動削除期間は利用方法を確認してから決める。

TODOでは「残っている仕様項目を決定する」という1行を削除し、上記の具体項目へ置き換える。Phase 3前に決める3項目をP0、後続フェーズで決める項目を各フェーズのP0/P1として配置する。

### 推奨実施順序

1. 仕様分割とID・時刻・エラー・ログ規約をADRで確定する。
2. APIエラー、requestId、UTC、構造化ログを実装し、既存テストを更新する。
3. プレイヤー人数・色重複・同順位規則を入力検証とテストへ反映する。
4. GitHub Actionsを追加し、クリーン環境で全品質チェックを通す。
5. TODOの該当P0を完了にし、後続判断を各フェーズへ移す。

この順序なら、Phase 3で追加されるカメラ状態・認識エラー・信頼度ログが、途中で異なるID・エラー・ログ形式を持つことを避けられる。

### Claudeへの確認依頼

- 初期版のプレイヤー上限を8人、色重複を禁止とする案でよいか。
- APIエラーを一括で辞書形式へ移行し、Webと既存テストも同時に更新する方針でよいか。
- ローカルログの提案値（30日または10MB×5世代）を初期既定値にしてよいか。
- CIはまずUbuntuのみを必須とし、macOS実機確認を別運用にする案でよいか。

---

## Phase 3着手前の修正報告（2026-11-13）

`TOCODEX.md` の指示に従い、Phase 3の実装には着手せず、着手前の修正だけを行った。

- AI間の質問用に誤って作成した `CLAUDE.md` を削除した。今後の報告・質問先はこの `TOCLAUDE.md` に統一する。
- 出目訂正でゴールが早まった場合、ゴール済みプレイヤーの後続ROLLとゲーム完了後のROLLだけを無効化し、それ以外のプレイヤーの出目を再適用するよう変更した。
- 訂正APIへ `dryRun` と `confirmInvalidatedRolls` を追加した。無効化対象を事前表示し、確認なしの確定要求には `roll_invalidation_confirmation_required` の409を返す。
- CORRECTION監査payloadへ、無効にしたROLLのイベントIDと理由（`player_already_finished` / `game_completed`）を保存する。訂正のUndoで元の全ターンを復元する。
- ゴールを取り消す方向の訂正は、後続ターンをUndoしてから訂正するよう案内して拒否する。
- ゴール済みプレイヤーの位置を戻した場合、現在の有効ターンにおけるゴール順から順位を詰め直す。
- ABORTEDゲームの位置訂正は中止状態を維持する。COMPLETEDゲームは、ゴールを取り消した場合だけ進行中へ戻し、ターン履歴から次の手番を決める。
- ゲーム作成と現在ゲーム設定をSQLiteの同一トランザクションへまとめた。旧開発DBで現在ゲームが未設定の場合は、最終更新ゲームを初期値として補う。
- READMEの引き継ぎファイル名を `TOCODEX.md` に修正し、異常終了PAUSEをUndoできることを運用上の注意として追記した。
- ADR 0002、ARCHITECTURE、TODOを更新した。復元UIの選択式改善はPhase 5の残件とした。

回帰テストには、3人・2人でゴールが早まる訂正、ゴール取消拒否、事前確認API、Undo復元、順位再計算、ABORTED/COMPLETEDの位置訂正を追加した。

Phase 2全体は `Complete phase 2 history and persistence` のコミットとしてPhase 3の差分から分離した。

---

## Phase 2 レビュー対応（2026-11-13）

`TOCHATGPT.md` のPhase 2レビューに対応した。

- Undo後の新入力は新ブランチへ分岐し、旧ターンをSQLiteに `active=0` で保持する。
- `_rebuild()` と操作再生を同じイベント適用処理へ統一し、ターンの状態バージョンをイベントの `state_version` から再構築する。
- 位置訂正で100へ移動する操作を拒否し、ドメイン不変条件違反をAPIの409へ変換する。
- ROLLイベントへ `playerId` を追加し、訂正後に後続手番が一致しない場合を検出する（その後の確定仕様は上のPhase 3着手前報告へ反映）。
- SQLite復元時の統計を有効ターンから再計算し、ヘビ・ハシゴ履歴を復元する。
- `HistoryAction` による操作単位の多段Undo/Redoへ変更した。出目訂正と復元も1操作としてUndo/Redoでき、スタックをSQLiteスナップショットへ保存する。
- 終了・中止ゲームの訂正・Undo/Redo・復元は `confirmEndedGame: true` を必須にした。
- `app_state.current_game_id` と `POST /api/games/{id}/activate` で現在ゲームを明示的に管理する。通常の保存だけでは切り替えない。
- FastAPI lifespanで進行中ゲームを検出し、`process_interrupted` の理由付きPAUSEを記録する。レスポンスの `interruption` と管理画面のバナーへ反映する。
- SQLiteは `BEGIN IMMEDIATE`、WAL、`synchronous=NORMAL` とし、既定DBパスをリポジトリルート基準の絶対パスへ変更した。
- UIへ操作確認、終了済みゲームの追加確認、Undo/Redoラベル、制御入力の位置訂正、理由・操作者入力、復元UIを追加した。
- 設計判断は `docs/decisions/0002-undo-redo-and-recovery.md` に記録した。
- 後続手番不一致時の方針は後続レビューで確定し、上のPhase 3着手前報告のとおり実装した。

回帰テストは44件まで拡充し、Ruff、mypy、pytest、Web型検査、Vite本番ビルドに成功した。

---

## Phase 2 実装引き継ぎ（2026-11-13）

履歴ブランチ、SQLite保存、全操作再生と競合防止、訂正・Undo/Redo・復元、ゲーム一覧と再開画面を実装した。外部機器操作とAPIキーは使用していない。

### 履歴ブランチ

- `docs/decisions/0001-history-branches.md` にADRを追加した。
- 初期ブランチは `main`。ターンIDはゲームID・ブランチID・ターン番号を材料にしたUUID v5。
- 出目訂正では分岐前の操作とターンをそのまま有効に保つため、分岐前のターンIDは親ブランチと同一。
- 訂正対象と後続操作は新しいブランチIDで再生し、新しいターンIDを生成する。
- 訂正前の操作は削除せず `active=false` で保持する。

### SQLite保存

- `infrastructure/persistence/sqlite.py` に `SQLiteGameRepository` を追加した。
- 既定DBは `var/commentator.sqlite3`。`COMMENTATOR_DB_PATH` で変更可能。
- `games`、`operation_events`、`turns`、`snapshots` を同じSQLiteトランザクションで保存する。
- 状態変更ごとのスナップショットを保存し、Repositoryを作り直した後もゲームを復元できる。
- APIはRepositoryから受け取ったコピーを変更して保存する。保存失敗時は503を返し、保存前の公開状態は変わらない。

### 操作再生と競合防止

- `OperationType` / `OperationRecord` / `replay_operations()` を追加した。
- 開始、一時停止、再開、保存終了、中止、出目、位置訂正を発生時刻付きで再生できる。
- ゲームレスポンスに `stateVersion` を追加した。
- 出目を含む全変更APIで `expectedStateVersion` を必須にした。不一致は409。
- 旧 `expectedTurnNumber` は補助的な互換チェックとして任意で残した。

### 訂正・Undo/Redo・復元

- 出目訂正は対象ターン以降を新ブランチで再計算し、統計も有効ターンから再構築する。
- 位置訂正イベントにはプレイヤー、訂正前後の位置、理由、操作者を保存する。
- Undoは現在系列の最後の操作を無効化し、Redoは同じ操作を再有効化する。
- `/api/games/{game_id}/restore` は指定した状態バージョンのスナップショットを復元し、その後の操作を新しいブランチで開始できる。
- 訂正、Undo/Redo、復元の監査イベントも操作ログへ残す。

### ゲーム一覧と再開画面

- `GET /api/games` を追加した。
- 管理者画面に保存終了したゲームの一覧、Undo/Redo、位置訂正、過去の出目訂正UIを追加した。
- SQLiteを再オープンする結合テストでプロセス再起動相当の復元を確認した。

追加・変更したAPI:

- `GET /api/games`
- `POST /api/games/{id}/position-corrections`
- `POST /api/games/{id}/turns/{turnId}/correction`
- `POST /api/games/{id}/undo`
- `POST /api/games/{id}/redo`
- `POST /api/games/{id}/restore`
- `start`、`pause`、`resume`、`suspend`、`abort`、`rolls` を含む変更要求は `expectedStateVersion` 必須。

検証は `apps/server/tests/test_history_persistence.py` を追加し、Ruff、mypy、pytest 34件、Webの型検査と本番ビルドが成功した。

残件はWebSocket配信、番号付きSQLite Migration基盤、バックアップ運用。複数プロセス運用は対象外で、APIは1ワーカーで起動する。

---

## Phase 2以前のレビュー修正報告（2026-11-11）

更新日: 2026-11-11

この文書は、Phase 2着手前のレビュー12項目に対する実装変更と検証結果をまとめたものです。行番号は変更でずれるため、ファイルとシンボル名で参照します。

## 1. ターンIDのブランチ対応

対象: [game.py](apps/server/src/commentator/domain/game.py) の `GameState.branch_id`、`roll()`、`replay()`

- 初期ブランチIDは `main`。
- ターンIDはUUID v5で生成し、名前の材料は `{game_id}/{branch_id}/turn/{turn_number}`。
- 同じゲーム・ブランチ・ターン番号での再生は同じIDを返す。
- 訂正履歴を別ブランチIDで再生すると、同じターン番号でも別IDになる。
- `replay(initial, rolls, started_at=..., branch_id=...)` でブランチを指定可能。初期状態はコピーし、元の状態を変更しない。
- レスポンスには `branchId` を追加。

確認: `test_branch_ids_and_deterministic_replay` で、訂正前後のIDの相違、同一ブランチ再生の一致、初期状態の不変を検証。

Phase 2で必要なこと: 訂正時のブランチID発行・保存、親子関係、分岐前ターンの共有、無効化された履歴の管理。現在は別ブランチIDを渡す仕組みまでであり、異なる出目列に同じブランチIDを再利用すること自体をドメインで検出する機能はない。

## 2. maximumPositionの定義

対象: [game.py](apps/server/src/commentator/domain/game.py) の `compute_statistics()`

ユーザー合意により、ヘビの頭へ到達したマスも最高到達位置に含める。

計算は、各ターンの「移動前位置・通常移動後位置・最終位置」の最大値を履歴全体で集計する。例えば `92 → 93 → 73` なら最高到達位置は93。

確認: `test_snake_head_counts_as_maximum_position`。定義を [ARCHITECTURE.md](docs/ARCHITECTURE.md) に記載。

## 3. 保存終了からの再開と中断記録

対象: [game.py](apps/server/src/commentator/domain/game.py) の `resume()`、`suspend()`

- 設計を実装に合わせ、`SUSPENDED → IN_PROGRESS` に統一。
- 再開時に開始時刻は変更しない。
- `suspended_at` は最後の中断時刻として保持し、再開時に消さない。
- `suspension_history` にすべての中断時刻を追記。
- レスポンスは `suspendedAt` と `suspensionHistory` を返す。

確認: `test_resume_keeps_all_suspension_times` で複数回の中断・再開後も記録が残ることを検証。

注: 再開時刻や停止時間の集計、操作イベントとしての永続化はPhase 2の対象。

## 4. イベントの型と超過時の扱い

対象: [game.py](apps/server/src/commentator/domain/game.py) の `EventType`、`TurnRecord.events`、`_events_for()`

`EventType(StrEnum)` を導入し、イベント一覧を `tuple[EventType, ...]` に変更した。JSONでは従来どおり文字列値になる。

分類規則:

- 通常は `normal_move`、超過時は代わりに `goal_overshoot`。
- 出目5・6には `large_roll`、1・2には `small_roll`。超過時にも保持。
- ヘビ・ハシゴを適用した場合は、それぞれ `snake`・`ladder`。
- 最終位置が100なら `goal`。
- ゴール未到達かつ最終位置が手前6マス以内なら `near_goal`。超過でも付与し、ゴール時には付与しない。

確認: パラメーター化した `test_events` の6ケースで通常移動、大小、超過、ヘビ、ハシゴ、ゴールを検証。

注: 首位交代や訂正など、まだ未実装のイベント種別は今後追加する。

## 5. アプリ状態の分離とRepository差し込み

対象: [main.py](apps/server/src/commentator/main.py)、[test_api.py](apps/server/tests/test_api.py)

- `create_app(board=None, repository=None)` を導入。
- `GameRepository` Protocolに `get()`、`save()`、`current()` を定義。
- 初期実装として `MemoryGameRepository` を追加。
- 盤面、Repository、排他ロックをアプリ生成ごとに用意。モジュール共有のゲーム辞書を廃止。
- 本番エントリーポイントは `app = create_app()`。
- APIテストはfixtureで毎回新しいアプリを作成する。
- 状態変更後に `repo.save(game)` を呼ぶ。

確認: `test_factory_isolation_validation_and_invalid_transitions` でRepositoryの差し込みと別アプリの空状態を検証。並行二重送信テストも継続して成功。

注: 現在のRepositoryは可変なGameStateを返すメモリ実装。DBトランザクション、失敗時のロールバック、複数プロセス間の競合制御はPhase 2で設計・実装する。

## 6. APIフィールド名の統一

対象: [schemas.py](apps/server/src/commentator/api/schemas.py)

- リクエスト共通モデルに `alias_generator=to_camel` を設定。
- 外部JSONはcamelCase、Python内部はsnake_caseに統一。
- 例: `displayName`、`spokenName`、`expectedTurnNumber`。
- テスト入力も `spoken_name` から `spokenName` へ変更。
- レスポンスは従来からcamelCaseであり、この規約を維持。

確認: API経由で `spokenName` を入力し、レスポンスへ反映されることを検証。

## 7. mypyの対象にテストを追加

対象: [pyproject.toml](pyproject.toml)、[tests](apps/server/tests)

- `tool.mypy.files` にsrcとtestsを指定。
- `current_player` や `jump` のOptional値は、テストで明示的にNoneでないことを確認してから参照。
- HTTPテストのヘルパーは、任意の `**dict[str, object]` をhttpxへ渡す方式をやめ、JSON入力を明示した形へ変更。
- READMEの実行コマンドは、設定された全対象を検査する `.venv/bin/mypy` に更新。

検証時点では14ファイルを検査し、型エラーなし。

## 8. 不足していたテスト

主な追加先: [test_review_regressions.py](apps/server/tests/test_review_regressions.py)

- ブランチごとのターンIDと決定的再生
- ヘビの頭を含む最高到達位置
- 複数回の保存終了・再開と中断履歴
- イベント分類（6ケース）
- ゴール済みと未ゴールが混在する場合の同順位
- READYからの再開など不正な状態遷移の409
- READYからの中止
- 中止後の開始拒否
- 一時停止中の出目入力拒否
- 空白名の拒否と名前の前後空白除去
- アプリごとの状態分離とRepository差し込み

既存の二重送信、出目訂正を想定した再生、盤面到達可能性などのテストも維持。

## 9. jump_by_startの事前構築

対象: [board.py](apps/server/src/commentator/domain/board.py)

- 呼び出しごとのdict構築を廃止。
- 盤面構築時に一度生成し、読み取り専用の `MappingProxyType` として保持。
- 移動、盤面の到達可能性検証、最短手数計算で同じマッピングを参照。
- 不変なBoardの `__deepcopy__()` は自身を返し、再生時に読み取り専用マッピングをコピーしようとする問題を回避。

## 10. 入力検証・依存関係・型

- [pyproject.toml](pyproject.toml) に直接依存 `pydantic>=2.9,<3` を追加。
- リクエストモデルに `str_strip_whitespace=True` を指定。
- 名前・色の必須文字列に加え、任意の表示名・読み方も、指定された場合は空文字を拒否。
- [main.py](apps/server/src/commentator/main.py) の `_apply_action` を `Callable[[], object]` で型付け。

## 11. 実行バージョンの指定

- [.nvmrc](.nvmrc): Node 22系
- [.python-version](.python-version): Python 3.13系
- [package.json](package.json): `node >=22 <23`
- [pyproject.toml](pyproject.toml): `requires-python >=3.13,<3.14`
- mypyの対象Pythonも3.13に変更。
- package-lock.jsonを同期。

これはメジャー／マイナー系列の指定であり、全依存関係や実行環境のパッチ版まで完全固定したものではない。検証環境はPython 3.13.3、Node 22.23.4。Node 22は一時実行で検証しており、MacのデフォルトNode自体は変更していない。

## 12. READMEと設計・ToDoの更新

[README.md](README.md) の冒頭を「ゲームエンジン、手動入力API、管理者・プレイヤー画面の初期実装が動作する段階」に更新。Python・Nodeの設定方法と、テストを含む型検査コマンドを追記。

[ARCHITECTURE.md](docs/ARCHITECTURE.md) へブランチID、最高到達位置、再開・中断履歴、イベント分類、API命名規約、アプリ生成方針を反映。

[TODO.md](TODO.md) へ完了項目とPhase 2の残作業を反映。

## 前回レビュー分の対応

今回の12項目の前提として、以下も実装済み。

- `compute_statistics(turns)` による履歴からの統計再計算。
- 時刻付き出目列と初期状態による `replay()`。
- UI送信中の無効化と送信ロック、APIの `expectedTurnNumber` チェックと排他制御。
- ハシゴ経由を含む `winningRolls`。75マスでは `[5]`。
- ゴールへ到達できないマスがある盤面の構築時拒否。
- 正常終了・中止後の「新しいゲーム」ボタン。

## 検証結果

直前の実装検証で以下が成功。

| 検証 | 結果 |
|---|---|
| `.venv/bin/ruff check apps/server` | 成功 |
| `.venv/bin/mypy` | src・tests計14ファイルでエラーなし |
| `.venv/bin/pytest -q` | 30件成功 |
| Node 22での `npm run build:web` | TypeScript型検査・Viteビルド成功 |

この報告書追加時には、上記テストを再実行していない。

## Phase 2へ残した範囲

- SQLite保存、スナップショット、異常終了後の復元。
- 訂正・Undo/Redoの操作と履歴ブランチの永続管理。
- 一時停止・再開・中止・位置訂正も含めた操作イベント再生。
- 全操作を対象にした状態バージョン。現在の期待番号チェックは出目入力のみ。
- ゲーム一覧と保存終了したゲームを開く画面。
- ポーリングからWebSocketへの切り替え。
- DBトランザクションと永続化失敗時の整合性。

現在の「保存終了」はメモリ上の状態遷移で、プロセス終了をまたぐ保存はまだ行わない。APIは単一プロセス・1ワーカーで運用する前提。
