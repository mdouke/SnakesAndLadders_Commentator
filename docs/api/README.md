# API契約の更新手順

APIを変更するときは、実装と同じ変更で次を更新する。

認識APIの契約は [recognition.md](recognition.md)、WebSocket配信は
[realtime.md](realtime.md)、共通エラーは [errors.md](errors.md) を参照する。

1. Pydanticのリクエストモデル（`api/schemas.py`）、レスポンスモデル（`api/responses.py`）、
   FastAPIエンドポイント
2. OpenAPIとTypeScript型を再生成する（下記）。`apps/web/src/api.ts` の呼び出しも合わせる
3. 正常系・エラー系のAPIテスト
4. エラーcodeを追加・変更する場合は `errors.md`
5. 外部利用者へ影響するフィールドや状態遷移は `ARCHITECTURE.md` またはADR

既存フィールドの意味変更や削除は、移行方針をADRへ記録する。APIエラーは `detail: { code, message, context, requestId }` を維持し、Webは表示文言ではなくcodeで分岐する。

## TypeScript型の生成

Webの型は手書きせず、サーバーのOpenAPIから生成する。

```sh
.venv/bin/python -m commentator.tools.export_openapi   # apps/web/openapi.json を更新
npm run generate:api                                    # apps/web/src/generated/api.ts を更新
```

- 両方の生成物をコミットする。`apps/web/src/types.ts` は生成型の別名だけを置く。
- レスポンスモデルは `extra="forbid"` で、ドメインの `to_dict()` と食い違うとAPIテストが失敗する。
- 古い生成物は次で検出する（ローカル5チェックとCIに含まれる）。
  - `pytest`: `openapi.json` が現在のアプリと一致するか（`test_api_contract.py`）
  - `npm run check:web`: `generated/api.ts` が `openapi.json` と一致するか（`check:api`）
- WebSocketのメッセージはルートとして表現できないため、`RealtimeMessage` と各メッセージの
  スキーマを `components.schemas` に追加している。
