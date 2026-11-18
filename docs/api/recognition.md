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
出目訂正APIで訂正したターンは `source: "corrected"` となり、`correctedFrom`
（訂正前の `value`、`source`、`confirmationId`）を返す。訂正していないターンでは `null`。
古い保存データで `source` がない出目は `manual` として読み込む。同じ
`confirmationId` は Undo 後も再登録できない。

自動確定では、投擲サイクルが始まった時点の `gameId`、`stateVersion`、手番を使って
登録する。途中で手動入力、Undo、ゲーム切替などが起きた場合は競合として確認待ちへ戻る。
