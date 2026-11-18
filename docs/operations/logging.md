# ログ運用

## 出力

サーバーは1行1 JSONをstdoutと `var/log/commentator.jsonl` へ出力する。request IDはAPIレスポンスの `X-Request-ID` と一致する。通常ログへAPIキー、Authorization、音声本文、LLMプロンプト全文、カメラ画像、プレイヤーの読み方を記録してはならない。

## 保存方式

`.env`またはプロセス環境変数で選ぶ。

| 変数 | 既定値 | 意味 |
|---|---:|---|
| `COMMENTATOR_LOG_RETENTION` | `rotate` | `rotate`または`permanent` |
| `COMMENTATOR_LOG_DIR` | `var/log` | ログ保存先 |
| `COMMENTATOR_LOG_RETENTION_DAYS` | `30` | rotate時に保持する最大日数 |
| `COMMENTATOR_LOG_MAX_BYTES` | `10485760` | rotate時の1ファイル最大bytes |
| `COMMENTATOR_LOG_BACKUP_COUNT` | `5` | rotate時のバックアップ世代数 |

`rotate`は、保持日数を超えたファイルを**サーバー起動時に**削除し、サイズと世代数でもローテーションする。長期間再起動しない間は日数による削除が走らない。`permanent`は日ごとにファイルを分け、アプリケーションから削除しない。permanentではディスク使用量が増え続けるため、空き容量の監視、研究計画に沿ったアクセス制御・バックアップ・削除を運用者が行う。

研究データとして永続保存する場合も、機微情報を通常ログへ出さない規約は変わらない。音声や将来の明示的な研究データセットは通常ログと分離する。

### 管理画面からの変更（2026-11-18 追加）

管理画面の「システムの状態」から、`rotate` と `permanent` を切り替えられる。選択は `var/log-settings.json` に保存し、すぐにログの出力先を設定し直す（再起動は不要）。環境変数 `COMMENTATOR_LOG_RETENTION` を設定している場合は、その値が優先され、画面からは変更できない。

同じ画面に、最近の警告・エラー（最大30件、メモリ上のみ）を表示する。表示するのはメッセージ、ロガー名、イベント名、エラーコードだけで、リクエスト本文などは保持しない。

不正な設定値は起動時にエラーとなる。起動時の `logging_configured` イベントで、実際に選ばれた保存方式と設定値を確認できる。
