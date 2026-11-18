# APIエラー契約

エラーはHTTPステータスと次のJSON本文を返す。

```json
{
  "detail": {
    "code": "state_version_conflict",
    "message": "ゲーム状態が更新されています",
    "context": {
      "expectedStateVersion": 2,
      "actualStateVersion": 3
    },
    "requestId": "8f92ba31-66ea-4e93-b138-c3b5429a44be"
  }
}
```

- `code`: クライアント分岐用の安定したsnake_case文字列
- `message`: 管理画面へ表示できる日本語メッセージ
- `context`: エラーごとの安全な補足値。リクエスト本文や秘密値は含めない
- `requestId`: `X-Request-ID`レスポンスヘッダーおよびサーバーログと同じ値

主な分類:

| HTTP | 用途 |
|---|---|
| 404 | ゲーム、スナップショット、操作が存在しない |
| 409 | 状態競合、不正な状態遷移、追加確認が必要 |
| 422 | JSON形式または入力値の検証失敗 |
| 503 | SQLiteなど一時的な永続化失敗 |

新しいエラーを追加するときは、既存の`code`の意味を変更せず、この文書とWebの分岐・テストを同じ変更で更新する。

## サイコロ認識

| HTTP | code | 意味 |
|---|---|---|
| 409 | `game_not_in_progress` | 自動確定または確認結果を登録できる進行中ゲームがない |
| 409 | `state_version_conflict` | 確認画面のゲーム状態が古い |
| 409 | `turn_conflict` | 確認画面の手番が古い |
| 409 | `current_game_conflict` | 確認対象とは別のゲームが現在選択されている |
| 409 | `duplicate_confirmation` | 同じ `confirmationId` が登録済み（Undo済みを含む） |
| 409 | `candidate_not_pending` | 現在は確認待ちではない |
| 409 | `stale_candidate` | `candidateId` が現在の確認候補と一致しない |
| 409 | `candidate_rejected` | 候補を振り直しとして却下した状態（認識状態の `errorCode`） |
| 409 | `camera_not_selected` | 開始時にカメラが選択されていない |
| 409 | `recognition_already_running` | 認識がすでに動作中 |
| 503 | `recognition_unavailable` | OpenCV等の追加依存がなく認識を利用できない |
| 503 | `camera_enumeration_failed` | カメラ一覧を取得できない |
| 503 | `camera_connection_failed` | 選択したカメラへ接続できない |
| 503 | `recognition_stop_timeout` | カメラ読込スレッドが停止時間内に終了しない |

認識状態の `errorCode` には、予期しないワーカー障害の
`recognition_worker_failed` と、内部状態競合の `recognition_state_conflict` も入る。

認識結果のゲーム登録に失敗した場合、認識状態は元の候補を保持した
`needs_confirmation` に戻り、その理由を `errorCode` に設定する。
