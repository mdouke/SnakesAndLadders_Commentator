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
