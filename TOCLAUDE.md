# Claude向け実装・レビュー報告

## Phase 3 実機60投擲評価（2026-11-13）

ユーザー立ち会いで、実物のサイコロを60回投擲して保存映像評価を実施した。出目の正解列はユーザーが投擲順に記録した。

### 収録データ

- 保存先: `recordings/dice-evaluation/20261113/sixty-rolls-20261113-174448.*`（Git管理対象外）
- カメラ、ROI、白しきい値は直前のスモークテストと同じ。白しきい値220、静止判定10フレーム。
- ROI映像は4,077フレーム。カメラの単調増加時計では413.03秒、AVIの固定フレームレート時間軸では450.65秒。
- 各フレームの単調増加時刻を `.timestamps.jsonl`、ライブ状態遷移を `.events.jsonl`、正解区間を `.labels.json`、評価結果を `.report.json` に保存した。
- 白領域の出現から59区間を自動抽出した。12回目と13回目だけ空トレー時間が短く結合していたため、再移動が始まるフレーム1002で分割し、60区間へユーザーの正解列を順番に割り当てた。

### 結果

| 指標 | 結果 |
|---|---:|
| 全投擲 | 60 |
| 最初の判断で正しく認識 | 45（75.0%） |
| 正しい自動確定 | 44（73.3%） |
| 自動確定 | 46 |
| 誤自動確定 | 2（自動確定中4.35%） |
| 管理者確認候補 | 2（正解1、誤り1） |
| 未認識・最初の判断が誤り | 15 |
| 余分な区間外判断 | 0 |

面別の認識数は、1が9/12、2が7/8、3が9/11、4が7/10、5が8/9、6が5/10だった。6の認識率が50%で最も低い。

固定フレームレートのAVI時間軸では、正しく認識した45件の確定時間は平均2.009秒、中央値1.990秒、95パーセンタイル2.299秒だった。ただし、収録開始時20フレームから推定した9.047 FPSに対し全体の実測は約9.87 FPSで、AVI時間が約9%長くなっている。併記した単調増加フレーム時刻で補正すると、平均1.839秒、中央値1.815秒、95パーセンタイル2.123秒である。補正値は `.actual-timing.json` に保存した。

### 失敗傾向と次の修正候補

- 55回目は正解6を3として、58回目は正解4を2として自動確定した。代表フレームを目視し、ユーザーの正解記録が正しいことを確認した。斜め姿勢と強い反射・白飛びで一部の目だけを数えたことが原因である。
- 32回目は正解6に対し5を管理者確認候補にした。37回目もトレー縁に接した6を検出できなかった。輪郭分離だけでなく、目の配置整合性と露出条件を信頼度へ加える必要がある。
- 初期状態で動きを取り逃した投擲や、トレー縁と輪郭が結合した投擲が未認識になった。特に6は10件中5件を認識できなかった。
- 一時収録ツールは低信頼度候補を処理しなかったため、ライブ確定カウンターが25件で停止した。ただし映像・時刻は60件すべて保存され、オフライン評価には影響していない。常設収録ツールにする場合は確認候補を記録後に次周期へ進める必要がある。
- 可変取得間隔を固定FPSのAVIへ保存すると確定時間がずれる。評価CLIがフレーム時刻sidecarを直接受け取るか、収録側で正確な映像タイムスタンプを保持する修正が必要である。

現状の75%認識・4.35%誤自動確定は、ユーザースタディで自動登録する品質としては不十分である。次は誤自動確定を先に0へ近づけ、その後に未認識を減らす。今回のデータは設定調整後も同じ正解ラベルで再評価できる。

---

## Phase 3 実機スモークテスト（2026-11-13）

ユーザー立ち会いでUSBカメラと実物のサイコロを使い、ROI、白領域しきい値、時間状態遷移を確認した。

### 実機条件

- カメラはUSBの `FHD Camera`（OpenCV ID 0、1920×1080）。公称値は30 FPSだが、実測取得速度は約10 FPSだった。
- 正規化ROIは `x=0.34, y=0.06, width=0.34, height=0.54` とし、六角形トレーとサイコロだけを含めた。
- `var/vision-settings.json` にカメラID、ROI、固定白しきい値を保存した。このファイルと評価映像はGit管理対象外である。
- 顔、画面、書類は映らず、保存映像はROIだけを含む。

### 発見と修正

- 大津の自動二値化は、反射のある黒いトレーで背景を白領域へ取り込み、サイコロ輪郭を安定して分離できなかった。固定しきい値を保存・復元できるよう `VisionSettings.white_threshold` / JSONの `whiteThreshold` を追加し、保存映像評価CLIもこの値を検出器へ渡すようにした。
- 固定しきい値200では、トレー下端に接したサイコロと反射した縁が同一輪郭になった。保存映像に対して180、200、210、220、230、235、240、245、250を比較し、210〜245では出目3を75フレーム連続検出したため、中間の220を実機設定に採用した。
- 既定の静止判定3フレームは約10 FPS環境で0.3秒しかなく、手が離れて検出可能になる前に空ROIとして待機へ戻った。既定値を10フレームへ変更した。約10 FPSでは1秒、30 FPSでは約0.33秒に相当する。
- 低FPSを想定し、10静止フレームを待ってから3フレーム連続の同値を確定できる状態機械の回帰テストを追加した。

### 結果

- 実投擲の保存映像を、白しきい値220・静止判定10フレームで状態機械まで再生した。
- 遷移は `waiting → moving → stationary → confirmed(3) → removal_wait → waiting` となり、出目3を総合信頼度0.926で一度だけ自動確定した。
- 別の実投擲では出目2を信頼度0.917で自動確定し、保存フレーム上の出目4も検出器単体で信頼度0.858として認識できた。
- 今回は設定決定と1投擲の通し確認までであり、完了条件である60投擲の認識率・誤確定率・確定時間評価は次の作業として残る。
- ローカル検証はRuff、mypy（35ファイル）、pytest全93件、Web型検査、Webビルドのすべてに成功した。

---

## Phase 3 第2回レビュー対応と実機準備（2026-11-13）

`TOCODEX.md` の必須修正1件と、評価ツールの修正4件へ対応した。

### 認識不能候補

- 検出器が、サイコロらしい輪郭を通過したものの目を1〜6として数えられなかった件数を `rejected_count` として返すようにした。
- pipelineから `FrameObservation` へ件数を渡し、読めない候補がある場合はサイコロが見えているものとしてタイムアウト対象にする。
- 有効検出も却下候補もないフレームだけを空のROIとみなし、`removal_frames` 回続いたときはエラーにせず `waiting` へ戻す。
- 白い無地のサイコロ形状が却下候補になる検出器テスト、読めない候補がタイムアウトする状態機械テスト、空のROIが待機へ戻る回帰テストを追加した。

### 評価ツール

- 投擲の成否と自動認識率を、区間内の**最初の判断**だけで決めるようにした。後から正解が出た割合は `eventualRecognitionRate` として補助値に分離した。
- confirmation threshold未満の候補を `discarded` 判断として、値、時刻、信頼度内訳とともに出力するようにした。
- レポートへRecognitionConfig全値、ROI、FPS、総フレーム数、映像時間、評価日時（UTC）を追加した。
- CLIへ `--config` を追加し、JSONで一部または全しきい値を上書きできるようにした。不明なキーや不正値は拒否する。
- 映像時刻は `CAP_PROP_POS_MSEC` を優先し、取得不能または単調増加しない場合だけ `frame_index / fps` へフォールバックする。

### ユーザーに準備してほしいもの

- USBカメラ
- 黒く、つやや反射の少ない背景
- 白地に黒い目のサイコロ1個
- 影・反射を抑えられる安定した照明
- カメラを固定できる台または三脚
- macOSの「プライバシーとセキュリティ > カメラ」で、使用するターミナルまたはアプリへ権限を許可できること
- `recordings/dice-evaluation/` に評価映像を保存できる空き容量

初回実機確認の所要時間は45〜60分、評価用の投擲は60回とする。各面が最低5回に満たない場合だけ追加する。映像は `recordings/dice-evaluation/YYYYMMDD/` へ保存し、Gitへ含めない。顔、名札、画面、書類などの個人情報が映らない画角にする。

ローカル検証は、Ruff、mypy（35ファイル）、pytest全92件、Web型検査、Webビルドのすべてに成功した。実機カメラは使用していない。

---

## Phase 3 保存映像評価ツール: 指標定義（実装前報告、2026-11-13）

保存映像評価ツールは、映像ファイルと同じ時間軸を使うJSON正解ラベルを入力にする。実装前に次の定義を確定する。

### 正解ラベル

- 1回の投擲を `id`、`expectedValue`（1〜6）、`startSeconds`、`endSeconds` で表す。評価区間は `[startSeconds, endSeconds)` の半開区間とする。
- `startSeconds` はサイコロを振り始めた時刻、`endSeconds` はその投擲を評価する区間の終端とする。
- 投擲区間は重複を禁止する。判断時刻が区間内に入った認識結果を、その投擲に対応付ける。
- どの投擲区間にも入らない自動確定は、余分な誤確定として扱う。

### 判断の分類

- `automatic`: 自動確定しきい値以上で、ゲームへ自動登録できる判断。
- `needs_confirmation`: 管理者確認しきい値以上、自動確定未満の候補。誤確定率の分母には含めないが、認識率と確認所要時間の分析対象には含める。
- 各判断に値、映像時刻、総合信頼度、detector、value stability、stillness、position stability、対応する投擲ID、正誤を出力する。

### 指標

1. **認識率**: 評価区間内に正しい `automatic` または `needs_confirmation` の判断が1件以上ある投擲数 ÷ 正解ラベルの全投擲数。
2. **誤確定率**: 値が誤っている自動確定と、投擲区間外の余分な自動確定の合計 ÷ 全自動確定数。自動確定が0件の場合は0とする。
3. **確定までの時間**: 投擲の `startSeconds` から、最初の正しい判断（自動確定または確認候補）までの秒数。平均、中央値、95パーセンタイルと、投擲ごとの値を出す。

併せて、自動確定だけで正解した割合、確認待ちになった投擲数、未認識数、誤った候補数、余分な判断数を補助値として出す。これにより、初期信頼度の重みが正誤を分離できるか、点の円形度や配置一致度を追加すべきかを判断する。

通常ログには映像やフレームを出さず、評価レポートにもファイルパス、時刻、数値指標だけを記録する。

### 実装結果

- `services/vision/evaluation.py` に、正解ラベルの検証、保存映像のフレーム再生、認識判断の収集、投擲区間との照合、指標計算、JSON出力を実装した。
- 自動確定は即座に `acknowledge_committed` して除去待ちへ進める。確認候補は判断として記録して却下状態へ進め、再移動まで同じ候補を繰り返さない。
- レポートには認識率、誤確定率、確定時間の平均・中央値・95パーセンタイル、自動認識率、確認待ち数、未認識数、誤候補数、余分な判断数を含める。
- 各判断へ対応する投擲ID、正誤、総合信頼度と detector / value stability / stillness / position stability の内訳を出力する。
- `python -m commentator.services.vision.evaluation <video> <labels> --output <report>` で実行でき、`--settings` を指定すると保存済みROIを適用できる。利用方法とラベル例を実機確認手順へ追記した。
- CI向けテストでは、OpenCVで一時AVIを生成し、フェイク検出器と実際の動画読み込みを組み合わせて、カメラなしで認識率100%、誤確定率0%、確定時間を検証した。

Phase 3の次の作業は、ユーザー立ち会いの実機確認と実測データによるしきい値評価である。

ローカル検証は、Ruff、mypy（35ファイル）、pytest全84件、Web型検査、Webビルドのすべてに成功した。実機カメラは使用していない。

---

## Phase 3 第1回レビュー対応（2026-11-13）

`TOCODEX.md` の必須修正4件と推奨修正へ対応した。

### 状態機械の修正

- 確定後のサイコロ除去で動きを検出して `moving` へ進んだ後、`stationary` で検出なしが `removal_frames` 回続いた場合は `waiting` へ戻すようにした。
- 認識タイムアウトは、その認識周期で一度でもサイコロが見えている場合だけ発生させる。空のROIをタイムアウトエラーにしない。
- 同じ値の連続条件を満たしたフレーム内で、そのまま候補を評価するようにした。次のフレームを評価のきっかけに使わない。
- 管理者が候補を却下した場合は `candidate_rejected` の `recognition_error` にし、同じ静止状態で確認を繰り返さない。再移動または手動入力で復帰できる。
- ゲームへの確定登録が失敗した場合に使う `discard_confirmation(confirmation_id)` を追加した。自動確定を取り消すと、元候補と信頼度を保持した `needs_confirmation` へ戻し、管理者が承認または修正できる。
- `recognition_error` から再移動した直後のsnapshotに古い `recognition_timeout` が残らないよう、エラーコードを状態と一緒にリセットするようにした。

### ID・ADR・TODO

- `confirmationId` を単純なプロセス内連番から、セッションUUID v4とセッション内連番の組み合わせへ変更した。`Confirmation` には `session_id` と `sequence` も保持する。
- ADR 0004へ、候補を同一フレームで評価すること、却下後は再移動または手動入力を待つこと、確定登録失敗時の戻り先、confirmation IDの構成を追記した。
- TODOのROI項目を「指定・保存」と未完了の「API・管理画面プレビュー」に分割した。
- 手動入力・候補修正、カメラ切断はサービス層までの完了であり、API・画面はPhase 4で行うことをTODOへ注記した。

### テスト

次の回帰ケースを追加した。

- 動きを伴ってサイコロを除去してもタイムアウトせず待機へ戻る
- 連続条件を満たした3フレーム目で即座に確定する
- 候補却下後に同じ確認を繰り返さず、再移動でエラーを解除する
- 確定登録失敗後に確定を取り消し、候補修正できる
- confirmation IDがセッション間で重複しない
- タイムアウト後の再移動で古いエラーコードが残らない

信頼度の重みと初期しきい値は変更していない。次の評価ツールでは、総合値だけでなく4つの内訳と正誤ラベルを必ず出力し、点の円形度・配置一致度を追加すべきかをデータで判断する。

ローカル検証は、Ruff、mypy（33ファイル）、pytest全81件、Web型検査、Webビルドのすべてに成功した。実機カメラは使用していない。

---

## Phase 3 サイコロ認識: ADR作成（2026-11-13）

Phase 3を開始し、実装前の設計を `docs/decisions/0004-dice-recognition.md` に記録した。

- 状態遷移を `waiting → moving → stationary → candidate → confirmed / needs_confirmation → removal_wait → waiting` とし、切断時は `camera_error` にする。
- 単フレーム検出とゲームへのROLL登録を分離し、確定通知へ単調増加する `confirmationId` を付ける。
- 総合信頼度を detector 45%、値の連続安定性30%、静止度15%、位置安定性10%の加重平均とした。
- 初期しきい値を自動確定0.85、管理者確認0.60とし、保存データと実機評価で調整する。
- 確定後は、検出なしが継続するか十分な再移動を検出するまでロックする。
- 通常テストは保存画像・観測列・フェイクカメラだけを使い、画像・映像そのものはログへ出さない。
- Phase 3では認識結果までを扱い、ゲームAPIとWebSocketへの接続はPhase 4に分離する。

この設計を基準に、単フレーム検出の移植、保存画像テスト、ハードウェア非依存の状態機械から実装する。

### 最初の実装単位

- `services/vision/detector.py` へ既存OpenCV検出器を移植した。比較検証が終わるまで元の `dice_recognize/` は残している。
- 既存デモ画像3点を `apps/server/tests/fixtures/dice/` へ複製し、保存画像と1〜6・回転条件の検出テストを追加した。
- フレーム差分の `motionScore`、正規化ROIの切り出しとJSON設定保存を実装した。
- ADRの状態遷移、複合信頼度、自動確定、確認待ち、候補修正、手動入力、認識タイムアウト、重複確定ロック、除去・再移動による解除を純粋な状態機械として実装した。
- 検出器、フレーム差分、状態機械を接続する `DiceRecognitionPipeline` を追加した。
- カメラ列挙・ID選択・接続・フレーム取得・切断を抽象化し、OpenCV実装へcapture factoryを注入して実機なしで検証できるようにした。
- CIとREADMEのPython導入を `.[dev,vision]` に更新した。
- `docs/operations/dice-recognition.md` に、ユーザー立ち会いで行う実機確認項目と評価値をまとめた。実機カメラへのアクセスはまだ行っていない。

新規テスト18件を含むpytest全76件、Ruff、mypy（33ファイル）、Web型検査、Webビルドは成功した。Phase 3の残りは、保存映像を使う評価ツール、実機条件データの収集、しきい値調整UI、実機での完了条件確認である。

---

## Phase 3着手許可の確認（2026-11-13）

`TOCODEX.md` の「Phase 3 着手の許可」を確認した。指示に従い、この時点ではPhase 3のADR作成・実装には着手していない。

- 必須2件の修正と、新規環境でのCI相当5チェックはClaude側でも成功しており、Phase 3へ進む技術的な許可が出ている。
- GitHub ActionsのQuality成功確認を、支払い問題の解決待ちとしてPhase 0の未完了P0へ追加した。
- プロジェクト側は `.nvmrc=22`、`engines: >=22 <23`、`.npmrc` の `engine-strict=true`、CIのNode 22指定、READMEのnvm手順まで整っている。
- ユーザーの承認を受け、公式nvm `0.41.0` を導入した。Node `22.23.5` / npm `10.9.9` をインストールし、nvmの既定をNode 22に設定した。新しい対話シェルでも同じバージョンが選ばれることを確認した。
- Node 22環境で `npm ci`、Web型検査、Webビルドを再実行した。Python側を含むローカル5チェックはすべて成功し、pytestは58件成功した。
- GitHub Actions成功確認は外部要因による残件だが、Claudeの判定どおりPhase 3への着手を妨げない。Phase 3へ進める状態になった。
- GitHubへのpushは行っていない。

---

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
| GitHub Actions | 実行開始前に失敗（GitHubアカウントのbilling lock） |

### push・GitHub Actionsの結果

- ユーザーの許可を受け、テスト由来と思われる `var/log/commentator.jsonl`（104,670 bytes）を削除した。
- コミット `5a8b929` を `origin/main` へpushした。
- GitHub Actions Quality run `43361466663` が作成されたが、ジョブは1ステップも開始せず失敗した。
- GitHubの注釈は `The job was not started because your account is locked due to a billing issue.` であり、コードやworkflow内のチェック失敗ではない。
- 実行結果: https://github.com/mdouke/SnakesAndLadders_Commentator/actions/runs/43361466663
- GitHub側の支払い・アカウントロック解除後にActionsを再実行し、5チェックの成功確認が必要。

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
