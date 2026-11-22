# 再開メモ

最終更新: 2026-11-22（作業を中断した時点の状態）。次に作業を始めるときは、まずこのファイルを読む。
詳しい経緯は [TODO.md](TODO.md)、設計は [ADR 0005](docs/decisions/0005-commentary-and-speech.md)（実況・読み上げ）と [ADR 0006](docs/decisions/0006-vlm-dice-reading.md)（サイコロの読み取り）にある。

## いまの状態

- ブランチ: `main`。テスト 480 件・ruff・mypy・画面の型チェックとビルドは通っている。
- GitHub（origin）への push はしていない。
- `google-tts` ブランチは `main` に早送りでマージ済み（ブランチは残してある。不要なら削除してよい）。
- `git stash` に1件: Cloud 経由 Gemini の最初の試作（取りやめたもの）。不要なら `git stash drop`。
- カメラでのサイコロ認識の開発は終わり（2026-11-19 ユーザー判断）。

### 実験の構成（2026-11-22 決定）

- 実況: Claude（`var/commentary-settings.json`）。
- 読み上げ: **macOS の音声**（`var/speech-settings.json` の provider が `macos`、標準の音声、速さ 230）。
  実況の文を外部に送らずに済む。WaveNet・Gemini は使わない（経緯は下の「これまでの経緯」）。
- サイコロ: OpenCV＋トレーの自動検出（`var/vision-settings.json` の `trayDetection: true`）。
  Claude での読み取り（`vlm` / `hybrid`）を使うかは、実機で試してから決める（TODO.md の Phase 6.5）。

## 起動

```bash
npm start          # API と画面をまとめて起動し、管理者画面とプレイヤー画面を開く（Ctrl+C で停止）
```

Finder で `start.command` をダブルクリックしてもよい。秘密情報や設定ファイルを直接書き換えたら起動し直す
（サーバーは起動時に読んだ設定を覚えていて、管理画面で保存するとその内容でファイルを上書きする）。

## 次にやること（上から順に）

1. 実機で通しのゲームをして、2026-11-22 の変更を確かめる（まだ実機では見ていない）:
   - 読み上げ: macOS の音声が文ごとに読まれ、話し始めが速いか（ログ `speech_finished` の `firstAudioMs`。目安は約2秒）。
   - プレイヤー画面: 音が出るまで「実況を準備しています」のままで、声と同時に実況の文が出るか。
   - 再開: 前のターンのサイコロをトレーに置いたまま再開・カメラ起動しても、そのサイコロを読まないか。
   - 管理画面: 「履歴と訂正」を見出しのクリックで折りたためるか。
2. ユーザースタディの準備（項目は TODO.md の「ユーザースタディの準備」）:
   - 同意書に、外部に送るデータを書く: トレーの画像 → Anthropic（Claude での読み取りを使う場合）、
     ゲームの状況とプレイヤー名 → Anthropic（Claude の実況）。読み上げは macOS なので外部に送らない。
   - 実機でトレーの自動検出と Claude のサイコロ読み取りを試し、使う方式を決める。
3. 必要なら GitHub に push する。

## 秘密情報（`var/secrets.env`、Git 管理外。値はここに書かない）

| 変数 | 用途 |
|---|---|
| `ANTHROPIC_API_KEY` | Claude の実況、Claude でのサイコロ読み取り |
| `GOOGLE_TTS_API_KEY` | WaveNet 用だったが、2026-11-22 の片付けのあと無効になった（API Key not found）。実験では使わない。行は残っている |
| `OPENAI_API_KEY` | 空（使っていない） |

- `GEMINI_API_KEY` と `GOOGLE_APPLICATION_CREDENTIALS`（サービスアカウントの鍵ファイル）は 2026-11-22 に削除した。
  Google Cloud（`sal-commentator`）側のサービスアカウント・鍵・API キー、AI Studio の Gemini API キーも削除済み。
  Google の読み上げを再び使うには、キーを作り直す必要がある（削除した API キーは 30 日以内なら Cloud Console の
  「認証情報」で復元できる）。
- この Mac の Python は証明書の一覧を持たないため、HTTPS は certifi を使っている（`pyproject.toml` の依存に記載済み）。

## これまでの経緯（2026-11-22）

- 読み上げ:
  - Gemini TTS は Flash-Lite（`gemini-2.5-flash-lite-preview-tts`）でも、実況文にないことを話し始めることがあった（生成型のため）。
  - いったん WaveNet に決めたが、片付けのあとキーが無効になったため、macOS の音声に変更した。
  - macOS の音声も Google と同じく文ごとに作って1文目からすぐ再生するようにした（2文先まで並行して作る）。
  - どの声でも、途中の文で失敗したら続きの文から次の声で読む（Gemini → WaveNet → macOS）。
  - ログの `speech_finished` に `firstAudioMs`・`durationMs`・`provider`・`voice` を記録する
    （[docs/operations/logging.md](docs/operations/logging.md) の「読み上げのログ」）。
- プレイヤー画面: 読み上げの状態に `preparing`（音声を準備中）を加え、音が出始めたときに実況の文を表示する
  （`revealedRequestId`。読み上げがオフ・失敗のときはすぐ表示、20秒で必ず表示）。管理画面は今までどおりすぐ表示する。
- サイコロ認識: 再開・カメラ起動の直後は画面が静止するまで動きを数えない。置かれていたサイコロの位置と目を覚え、
  同じ位置・同じ目で止まったものは読まない（1秒以上見えなくなったら取り除いたとみなす）。
  原因は、カメラの明るさの自動調整などの動きを投擲と取り違え、前のターンの 6 を読んだこと。
- 管理画面: 「履歴と訂正」を折りたためるようにした（開閉はブラウザが覚える）。

## 測った値

1文あたり音声ができるまで（2026-11-19）:

| 読み上げ | 時間 |
|---|---|
| Google WaveNet | 約0.35秒 |
| Gemini 2.5 Flash-Lite TTS（Cloud 経由） | 2.3〜3.0秒 |
| Gemini 2.5 Flash TTS（Cloud 経由） | 2.6〜8.3秒、20秒で時間切れのことも |
| macOS の標準音声（全文を一度に作る、旧方式） | 実況1つの全文で7〜10秒 |

macOS の音声を文ごとにした後（2026-11-22、この Mac、速さ 230、4文の実況、音は出さずに測定）:
話し始めまで約1.9秒（全文を一度に作ると約2.9秒）、1文を作るのに約1.7〜1.9秒、文の間の隙間は約0.02秒。

## つまずいた点（同じ失敗を避けるため）

- Gemini API（AI Studio）の読み上げは、有料枠でも1日100回まで。文ごとに送ると1ゲームも持たない。
- Google Cloud 経由の Gemini は APIキー（サービスアカウントに紐づけたキーも含む）を受け付けない。鍵ファイル（OAuth）が必要。
- Google Cloud でキーを削除すると、反映まで少し時間がかかる。削除直後に動いても、あとで無効になることがある。
- サーバーの起動中に `var/*.json` を直接書き換えても効かない。管理画面で保存すると古い内容で上書きされる。
- ログは個人情報を出さないよう決められた項目だけを書く（`infrastructure/logging/setup.py`）。失敗理由は `reason` に出る。
