# 再開メモ

最終更新: 2026-11-22（作業を中断した時点の状態）。次に作業を始めるときは、まずこのファイルを読む。
詳しい経緯は [TODO.md](TODO.md)、設計は [ADR 0005](docs/decisions/0005-commentary-and-speech.md)（実況・読み上げ）と [ADR 0006](docs/decisions/0006-vlm-dice-reading.md)（サイコロの読み取り）にある。

## いまの状態

- ブランチ: `main`（2026-11-22 に `google-tts` を早送りでマージ済み。`google-tts` ブランチは残してある）
  - `google-tts` で入れたもの: Google Cloud Text-to-Speech（WaveNet など）、Gemini の読み上げ
    （Gemini API 経由と Google Cloud 経由、失敗時の切り替え順、上限エラー時の待機）、
    途中で失敗したら続きの文から次の声で読む仕組み、読み上げログの `firstAudioMs`・`voice`。
    テスト 474 件・ruff・mypy は通っている。
  - `git stash` に1件: Cloud 経由 Gemini の最初の試作（APIキーでは動かず取りやめたもの）。不要なら `git stash drop`。
- GitHub（origin）への push はしていない。
- カメラでのサイコロ認識の開発は終わり（2026-11-19 ユーザー判断）。

## 起動

```bash
npm start          # API と画面をまとめて起動し、管理者画面とプレイヤー画面を開く（Ctrl+C で停止）
```

Finder で `start.command` をダブルクリックしてもよい。秘密情報や鍵ファイルを書き換えたら起動し直す。

## 秘密情報（`var/secrets.env`、Git 管理外。値はここに書かない）

| 変数 | 用途 |
|---|---|
| `ANTHROPIC_API_KEY` | Claude の実況、Claude でのサイコロ読み取り |
| `GOOGLE_TTS_API_KEY` | Google Cloud の WaveNet など。2026-11-22 の片付け後に無効になった（実験では使わない） |
| `GEMINI_API_KEY` | （2026-11-22 削除）Gemini API 経由の Gemini 読み上げ。1日100回しか使えない |
| `GOOGLE_APPLICATION_CREDENTIALS` | （2026-11-22 削除）Google Cloud 経由の Gemini 読み上げ用の鍵ファイルのパス |

- この Mac の Python は証明書の一覧を持たないため、HTTPS は certifi を使っている（`pyproject.toml` の依存に記載済み）。

## 次にやること（上から順に）

1. ~~Gemini のモデルを `gemini-2.5-flash-lite-preview-tts` に変える~~（2026-11-22 済み。`var/speech-settings.json`）
2. ~~読み上げの改善~~（2026-11-22 済み、未コミット）:
   - 途中の文で失敗したら、続きの文から WaveNet で読む。
   - ログの `speech_finished` に `firstAudioMs`・`voice`・`provider`・`durationMs` を記録する
     （項目の意味は [docs/operations/logging.md](docs/operations/logging.md) の「読み上げのログ」）。
   - 実機（Flash-Lite）での話し始めまでの時間はまだ測っていない。
3. ~~実験で使う声を決める~~（2026-11-22 決定: **macOS の音声**（`var/speech-settings.json` の provider が `macos`、速さ 230））。
   いったん WaveNet に決めたが、キーが無効になったこともあり macOS に変更した。実況の文を外部に送らずに済む。
   以下は WaveNet に決めたときの理由（参考）:
   理由: 話し始めが速い（約0.4秒）うえ、渡した文をそのまま読む。Gemini Flash-Lite は実況文にないことを
   話し始めることがあった（生成型のため）。Gemini の実装は残すが、実験では使わない。
4. ~~`google-tts` を `main` にマージする~~（2026-11-22 済み。push はまだ）。
5. ユーザースタディの準備:
   - 項目は TODO.md の「ユーザースタディの準備」にまとめた（同意書に書く外部送信の内容、実機での確認）。
6. ~~後片付け（安全のため）~~（2026-11-22 済み）:
   - Google Cloud（`sal-commentator`）のサービスアカウントの鍵・紐づけた API キー・不要なサービスアカウント、
     AI Studio の Gemini API キーを削除した（ユーザーが操作）。
   - この Mac の鍵ファイルを削除し、`var/secrets.env` から `GEMINI_API_KEY` と `GOOGLE_APPLICATION_CREDENTIALS` を消した。
   - 残したキーは `ANTHROPIC_API_KEY` と `GOOGLE_TTS_API_KEY` だけ。WaveNet が使えることを確かめた（1文 0.47秒）が、
     その後 Google 側の削除が反映されて `GOOGLE_TTS_API_KEY` も無効になった（API Key not found）。実験は macOS の音声で行う。
   - Gemini の読み上げを再び使うには、サービスアカウントの鍵か Gemini API キーを作り直す必要がある。

## 測った値（2026-11-19、1文あたり音声ができるまで）

| 読み上げ | 時間 |
|---|---|
| Google WaveNet | 約0.35秒 |
| Gemini 2.5 Flash-Lite TTS（Cloud 経由） | 2.3〜3.0秒 |
| Gemini 2.5 Flash TTS（Cloud 経由） | 2.6〜8.3秒、20秒で時間切れのことも |
| macOS の標準音声 | 実況1つの全文で7〜10秒 |

実際のゲームでは、実況ができてから読み終わるまで 24〜33 秒かかっていた（Flash 使用時。読む時間は約10秒）。

## つまずいた点（同じ失敗を避けるため）

- Gemini API（AI Studio）の読み上げは、有料枠でも `gemini-3.8-flash-tts` が1日100回まで。文ごとに送ると1ゲームも持たない。
- Google Cloud 経由の Gemini は APIキー（サービスアカウントに紐づけたキーも含む）を受け付けない。鍵ファイル（OAuth）が必要。
- IAM でロールを付けてから使えるようになるまで約3分かかった。
- ログは個人情報を出さないよう決められた項目だけを書く（`infrastructure/logging/setup.py`）。失敗理由は `reason` に出る。
