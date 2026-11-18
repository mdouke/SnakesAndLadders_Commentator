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
| `commentary` | `{ "status": "idle" }` | 接続時のみ。Phase 6 で実況の内容を追加する |

型は OpenAPI の `components.schemas.RealtimeMessage`（`type` で判別する union）として公開し、
Web では `types.ts` の `RealtimeMessage` を使う。

## 順序と再接続

- 接続直後に、`game`、`recognition`、`commentary` の順で現在の状態が1件ずつ届く。
- `sequence` はサーバー全体で単調に増える番号である。接続ごとに見ると増え続けるが、
  欠番はありうる（他の接続の初期状態にも番号を使うため）。
- 各接続には、番号の順に届く。初期状態は、その後の更新と同じロックの中で送るため、
  初期状態より古い状態が後から届くことはない。
- HTTP 応答と WebSocket は、どちらが先に届くか決まっていない。Web は同じゲームなら
  `stateVersion` が大きい方を採用する（`realtime.ts` の `newerGame`）。
- 未送信のメッセージが64件を超えた遅い接続は、コード1013で切断する。他の接続の配信は
  止めない。クライアントは再接続し、新しい初期状態で置き換える。
- Web は切断されると0.5秒から最大5秒の間隔で再接続する。
