# Snakes & Ladders Commentator

実物の Snakes & Ladders と連動し、カメラで認識したサイコロの出目をもとにゲーム状態を管理し、LLM が実況コメントを生成・表示・読み上げるシステムです。

現在は要件整理と設計の段階です。既存のサイコロ認識プロトタイプは `dice_recognize/` にあります。本体へ移植して検証が終わるまでは参照用として残し、移植後に削除します。

## ドキュメント

- [全体構成・設計方針](docs/ARCHITECTURE.md)
- [開発 ToDo](TODO.md)

## 初期方針

- バックエンド: Python / FastAPI
- 管理者・プレイヤー UI: TypeScript / React
- 永続化: SQLite（抽象化し、必要なら PostgreSQL に移行可能にする）
- リアルタイム通知: WebSocket
- サイコロ認識: OpenCV
- LLM: Claude API / OpenAI API を共通インターフェースで切り替え
- 読み上げ・録音: OS/TTS アダプターと FFmpeg をバックエンドから制御

この技術構成は暫定です。実装開始時に `docs/decisions/` の ADR で確定事項を記録します。
