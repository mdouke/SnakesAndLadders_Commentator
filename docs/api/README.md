# API契約の更新手順

APIを変更するときは、実装と同じ変更で次を更新する。

1. PydanticリクエストモデルとFastAPIエンドポイント
2. `apps/web/src/api.ts` と `types.ts`
3. 正常系・エラー系のAPIテスト
4. エラーcodeを追加・変更する場合は `errors.md`
5. 外部利用者へ影響するフィールドや状態遷移は `ARCHITECTURE.md` またはADR

既存フィールドの意味変更や削除は、移行方針をADRへ記録する。APIエラーは `detail: { code, message, context, requestId }` を維持し、Webは表示文言ではなくcodeで分岐する。
