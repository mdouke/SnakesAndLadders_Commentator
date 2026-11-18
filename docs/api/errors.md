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

## ゲーム操作

| HTTP | code | 意味 |
|---|---|---|
| 409 | `ended_game_confirmation_required` | 正常終了したゲームの結果を変える操作に `confirmEndedGame: true` がない |
| 409 | `game_aborted` | 中止したゲームへの訂正・Undo/Redo・復元・手番変更。先に `POST /api/games/{id}/reopen` で中止を取り消す |
| 409 | `invalid_game_operation` | 現在の状態ではできない操作（メッセージに理由） |

## 盤面プリセット・設定

| HTTP | code | 意味 |
|---|---|---|
| 404 | `board_not_found` | 指定した盤面プリセットがない |
| 422 | `invalid_board` | 盤面の規則違反（範囲外、開始マス重複、ヘビ・ハシゴの向き、移動先が別の開始マス、ゴールへ到達できない）、不正なID、URLとIDの不一致 |
| 409 | `board_preset_read_only` | 標準の盤面（`data/boards`）を上書き・削除しようとした |
| 409 | `logging_settings_locked` | 環境変数でログ保存方式が固定されている、またはこのサーバーでは変更できない |

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
| 409 | `recognition_preview_unavailable` | 認識が動作しておらず、プレビューのフレームがない |
| 503 | `recognition_preview_failed` | プレビュー画像を作成できない |

認識状態の `errorCode` には、予期しないワーカー障害の
`recognition_worker_failed` と、内部状態競合の `recognition_state_conflict` も入る。

認識結果のゲーム登録に失敗した場合、認識状態は元の候補を保持した
`needs_confirmation` に戻り、その理由を `errorCode` に設定する。
