# サイコロ認識 HTTP API

認識サービスは候補と根拠を返し、ゲームへの ROLL 登録はゲームサービスが
`stateVersion` と手番を検査して行う。画像、映像、ROI画素はレスポンスや通常ログへ
含めない。

## 状態取得とカメラ操作

- `GET /api/recognition`: 認識状態、候補、信頼度、直近の確定、エラー、接続状態
- `GET /api/recognition/cameras`: `{ "cameras": [{ "id": 0, "label": "Camera 0" }] }`
- `POST /api/recognition/start`: `{ "cameraId": 0 }`。この明示操作で初めてカメラを開く
- `POST /api/recognition/stop`: カメラとバックグラウンド処理を停止する

OpenCVなどの `vision` 追加依存がない場合もサーバーは起動する。この場合、
`GET /api/recognition` は `available: false`、`state: "unavailable"`、
`errorCode: "recognition_unavailable"` を返す。

`needs_confirmation` の間だけ `candidateId` が返る。候補が更新されるとIDも変わる。

## ROIプレビュー

`GET /api/recognition/preview.jpg` は、認識スレッドが最後に処理したフレームに、ROIの枠（緑）と
検出したサイコロ（橙、読んだ目の数字つき）を重ねたJPEGを返す。幅は最大640px、
`Cache-Control: no-store`。管理画面は0.5秒ごとに取り直して表示する。

- 認識が止まっているときは409 `recognition_preview_unavailable`。
- 画像はこのレスポンスとしてだけ返し、ログやディスクには残さない。サーバーが保持するのは
  最新フレームへの参照1つだけで、認識の停止時に破棄する。

`?view=` で表示を切り替えられる（調整ダイアログ用）。

- `overlay`（既定）: 全体映像にROI、検出したサイコロ（橙）、目を読めなかった候補（赤、理由つき）
- `mask`: ROI内の外形マスク。白い部分がサイコロの面として扱われる
- `pips`: 候補ごとの補正後の面と目の計数マスク（ライブプレビューツールと同じ表示）

## 調整用の診断と設定

- `GET /api/recognition/diagnostics`: 最新フレームの数値と、よくある失敗へのヒント。
  映像サイズ、実測FPS、動き量、ROI内の明るさ（平均と99パーセンタイル）、外形しきい値の
  実際の値、白い部分の割合、検出したサイコロと読めなかった候補、`hints`（日本語の文）。
  認識が止まっているときは `running: false` で数値は `null`。
- `GET /api/settings/vision`: 今使っている値（`settings`）、ファイルに保存された値（`saved`）、
  既定値（`defaults`）、`unsaved`、`pendingRecognitionSettings`。
- `PUT /api/settings/vision`: 一部の項目だけを変更し、動作中の認識へすぐ反映する。
  ファイルには保存しない。形は `var/vision-settings.json` と同じ（`roi` と
  `cameraResolution` はキーごとに上書き）。`whiteThreshold`、`pipThreshold`、`exposure` は
  `null` で自動。範囲外や不明な項目、自動確定より高い確認しきい値などは422 `invalid_settings`。
  - ROI、画像しきい値: 次のフレームから使う（ROIを変えると動きの基準をリセットする）。
  - 判定のしきい値・フレーム数: 投擲の途中では変えず、待機または除去待ちに入った時点で使う。
  - 解像度・露出: 認識スレッドが次のフレームを読む前にカメラへ設定する。
  - カメラ: 認識中に変えた場合は、カメラをつなぎ直す。
- `POST /api/settings/vision/save`: 今の値を `var/vision-settings.json` へ保存する。
- `POST /api/settings/vision/revert`: `{ "target": "saved" }` で保存した値、
  `{ "target": "defaults" }` で既定値へ戻す（保存はしない）。
- `trayDetection: true` で、ROI の中からトレー（一番大きい暗い部分）を探し、その形の内側だけを
  認識と Claude への画像に使う。診断の `tray`（`enabled`、`found`、`lastAttemptFound`、`vertices`、
  `areaRatio`、`secondsSinceUpdate`）で状態が分かる。プレビューにはトレーの形を水色で描き、外側を暗くする。
- 認識を利用できない環境では、設定APIは503 `recognition_unavailable`。

## 読み取り方式（OpenCV / Claude）

ADR 0006。動きと静止は常に OpenCV で判定し、出目の読み方を切り替える。

- `GET /api/settings/vlm`: `readingMode`（`opencv` / `vlm` / `hybrid`）、`model`、
  `timeoutSeconds`、`maxRetries`、`saveImages`、`installed`、`credentialsConfigured`
  （キーの値は返さない）、`activeReadingMode`（今使っている方式）、`pendingReadingMode`。
- `PUT /api/settings/vlm`: 一部の項目だけ変更して `var/vlm-settings.json` に保存する。
  方式の変更は投擲の途中では行わず、次の待機または除去待ちで反映する。
- `POST /api/recognition/vlm/test`: 最新フレームを今すぐ Claude で読む。結果を返すだけで
  ゲームには登録しない。認識が止まっていれば409、読み取りに失敗すれば503 `vlm_<理由>`。
- `GET /api/recognition/vlm/readings`: 直近30件の読み取り（`status` は `used`、`discarded`
  （呼び出し中にサイコロが動いた）、`failed`、`test`）。

`GET /api/recognition` には `readingMode`、読み取り中の `readingId`（状態 `reading`）、
今の投擲の `recognition`（OpenCV の候補と Claude の答え）が入る。読み取りに失敗した時の
`errorCode` は `vlm_unreadable`、`vlm_timeout`、`vlm_auth` などになる。

## 候補操作

承認または修正:

```http
POST /api/recognition/candidate/confirm
Content-Type: application/json

{
  "gameId": "game-uuid",
  "candidateId": "session-id:candidate:1",
  "expectedStateVersion": 3,
  "expectedTurnNumber": 2,
  "value": 5
}
```

`gameId` は管理者が確認しているゲームを指定する。現在のゲームと一致しなければ409となる。
`value` を省略すると候補を承認して `source: "reviewed"`、候補と異なる値を指定すると
`source: "corrected"` になる。成功時は更新後のゲーム状態を返す。候補ID、ゲーム状態、
手番のいずれかが古い場合は409を返し、ROLLを登録しない。

振り直し:

```http
POST /api/recognition/candidate/reject
Content-Type: application/json

{ "candidateId": "session-id:candidate:1" }
```

ゲーム状態は変更せず、次の明確な動きから新しい投擲として扱う。

## ROLL の監査情報

`TurnRecord` は `source`（`manual`、`automatic`、`reviewed`、`corrected`）を返す。
認識由来の場合は `confirmationId`、`originalCandidate`、`confidence` も保持する。
`recognition` には読み取り方式（`opencv` / `vlm` / `hybrid`）、`readingId`、OpenCV の候補、
Claude の答え（`diceCount`、`value`、`confidence`、`note`、`model`、`durationMs`）、`vlmError` が入る。
出目訂正APIで訂正したターンは `source: "corrected"` となり、`correctedFrom`
（訂正前の `value`、`source`、`confirmationId`）を返す。訂正していないターンでは `null`。
古い保存データで `source` がない出目は `manual` として読み込む。同じ
`confirmationId` は Undo 後も再登録できない。

自動確定では、投擲サイクルが始まった時点の `gameId`、`stateVersion`、手番を使って
登録する。途中で手動入力、Undo、ゲーム切替などが起きた場合は競合として確認待ちへ戻る。
