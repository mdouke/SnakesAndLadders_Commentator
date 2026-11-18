# リアルタイム配信（WebSocket）

`/ws` に接続すると、ゲーム状態、認識状態、実況状態が JSON で届く。クライアントからの送信は
使わない（受け取っても無視する）。開発時は Vite が `/ws` をサーバーへ中継する。

## メッセージ

```json
{ "type": "game", "sequence": 12, "payload": { "id": "...", "stateVersion": 5, "...": "..." } }
```

| `type` | `payload` | 送られるとき |
|---|---|---|
| `game` | 現在のゲーム（HTTPの `GameResponse` と同じ形）。ゲームがなければ `null` | 現在のゲームの `stateVersion` が変わったとき、現在のゲームが切り替わったとき |
| `recognition` | `GET /api/recognition` と同じ形 | 状態、候補、`errorCode`、カメラ接続などが変わったとき（フレームごとには送らない） |
| `commentary` | 現在のゲームの最新ターンの実況（下記） | 実況の状態が変わったとき |
| `speech` | `GET /api/speech` と同じ形（読み上げの状態） | 読み上げを始めたとき、終わった・止めたとき、失敗したとき |

型は OpenAPI の `components.schemas.RealtimeMessage`（`type` で判別する union）として公開し、
Web では `types.ts` の `RealtimeMessage` を使う。

## 順序と再接続

- 接続直後に、`game`、`recognition`、`commentary`、`speech` の順で現在の状態が1件ずつ届く。
- `sequence` はサーバー全体で単調に増える番号である。接続ごとに見ると増え続けるが、
  欠番はありうる（他の接続の初期状態にも番号を使うため）。
- 各接続には、番号の順に届く。初期状態は、その後の更新と同じロックの中で送るため、
  初期状態より古い状態が後から届くことはない。
- HTTP 応答と WebSocket は、どちらが先に届くか決まっていない。Web は同じゲームなら
  `stateVersion` が大きい方を採用する（`realtime.ts` の `newerGame`）。
- 未送信のメッセージが64件を超えた遅い接続は、コード1013で切断する。他の接続の配信は
  止めない。クライアントは再接続し、新しい初期状態で置き換える。
- Web は切断されると0.5秒から最大5秒の間隔で再接続する。

## 実況（`commentary`）

`status` は `idle`、`generating`、`ready`、`failed` のいずれか。`generating` と `ready` は、
必ず `turnId` のターンの `requestId` の要求についての実況である。`text` は `ready` のときだけ入る。
`source` は `generated`（Provider の文）か `fallback`（失敗や不適切な応答をテンプレートの文で
置き換えた。理由は `fallbackReason`）。`revision` は再生成の回数（最初は0）。

| ゲームの変化 | 実況 |
|---|---|
| 出目の確定、Redo（同じゲームでターンが増えた） | そのターンの実況を作る（`generating` → `ready`） |
| 最新ターンの出目訂正（最新ターンが置き換わった） | 訂正後の結果で作り直す |
| 管理者の再生成（`POST /api/commentary/regenerate`） | 同じターンで作り直す。前の版は表示しない |
| Undo（ターンが減った） | `idle` に戻す。1つ前のターンの実況は作り直さない |
| 一時停止、再開、位置訂正 | そのまま残す |
| 中止、保存終了 | `idle`。中止の取り消しや再開でも作り直さない |
| 別のゲームへの切り替え、新しいゲーム、サーバー起動直後 | `idle`（今起きたことではないため実況しない） |
| 管理画面で実況をオフにした | `idle`。オンに戻すまで作らない |

生成が終わった時点で、その要求がもう最新でなければ、結果を捨てる（記録は `discarded`、
ログに `commentary_discarded`）。読み上げは話し始める直前にも最新かを確かめ、実況が変わったら
話している途中でも止める。方針の詳細は [ADR 0005](../decisions/0005-commentary-and-speech.md)。

## 読み上げ（`speech`）

`status` は `idle`、`speaking`、`error`。`kind` は `commentary`（実況）か `preview`（試聴）。
`requestId` は読み上げている実況の要求ID。プレイヤー画面はこれが表示中の実況と同じときだけ
「実況中」と表示する。音はサーバーを動かしている Mac から出る。
