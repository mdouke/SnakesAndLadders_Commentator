# 再開メモ

最終更新: 2026-11-23（作業を中断した時点の状態）。次に作業を始めるときは、まずこのファイルを読む。
詳しい経緯は [TODO.md](TODO.md)、設計は [ADR 0005](docs/decisions/0005-commentary-and-speech.md)（実況・読み上げ）と [ADR 0006](docs/decisions/0006-vlm-dice-reading.md)（サイコロの読み取り）にある。
実況のプロンプトは [docs/commentary-prompt.md](docs/commentary-prompt.md)、サイコロ認識の運用は [docs/operations/dice-recognition.md](docs/operations/dice-recognition.md)。

## いまの状態

- ブランチ: `main`。テスト 518 件・ruff・mypy・画面の型チェックとビルドは通っている。
- GitHub（origin）への push はしていない。
- `commentary-prompt`（実況プロンプト v2〜v5・高速モード）と `dice-recognition-fixes`（サイコロ認識の修正）は
  2026-11-23 に `main` へ早送りでマージ済み。`google-tts` も同様。どのブランチも残してある（不要なら削除してよい）。
- `git stash` に1件: Cloud 経由 Gemini の最初の試作（取りやめたもの）。不要なら `git stash drop`。
- カメラでのサイコロ認識の開発は終わり（2026-11-19 ユーザー判断）。

### 実験の構成（2026-11-22 決定）

- 実況: Claude Opus 5.5、プロンプトは `commentary-v5`（既定）、高速モード オン（`var/commentary-settings.json`）。
  生成は平均約2秒（2026-11-23 のゲーム、18回）。版は管理画面の「プロンプトの版」で切り替えられる。
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

1. 実機で通しのゲームをして、2026-11-23 の変更を確かめる:
   - 実況 v5: ハシゴ・ヘビを実際に踏んだ出目で、一言目が「危ない」「惜しい」にならないか。
   - サイコロ認識: トレーを少しずらしても外形がついてくるか。手入力になった出目があれば、ログの
     `recognition_*`（下の「2026-11-23 の作業」）で理由をたどる。
   - 二重に数えないか（確定直後にサイコロが揺れても、次の人の出目にならないか）。
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

## 2026-11-23 の作業

- 実況プロンプト（[docs/commentary-prompt.md](docs/commentary-prompt.md)）:
  - 版を設定で選べる（v1〜v5）。使った版は書き換えず、変えるときは新しい版を作る（`prompts/vN.py`、`outlook_vN.py`）。
  - v4: 「惜しい」「ひやり」にも感情を込める（`emotion`）、直前の実況5つを渡して繰り返さない、序盤は「始まったばかり」、
    続くチャンス（`stillInReach`）、出目と移動を毎回は言わない。
  - v5: ハシゴ・ヘビ・ゴールを実際に踏んだ出目は、避けたヘビなどより実際の結果の感情を優先する（v4 は「危なーい！」で始まった）。
  - 禁止語の誤検出を修正（「始まったばかり」の「ばか」）。普通の言葉は照合の前に取り除く（`safety.py` の `ALLOWED_WORDS`）。
  - 遊んだゲームを別の版で作り直して比べる: `.venv/bin/python -m commentator.tools.compare_commentary --version commentary-v5`
    （`var/prompt-comparisons/` に Markdown。1回の出目ごとに Claude を1回呼ぶ）。
- 高速モード（`fastMode`、Opus 5.5 のみ、料金2倍）: 生成が平均 4.8 秒 → 2.0 秒。記録のモデル名は `claude-opus-5-5 (fast)`。
- サイコロ認識（[docs/operations/dice-recognition.md](docs/operations/dice-recognition.md)）:
  - トレーの外形を取り直したあと、手がトレーを横切らずに投げ込まれたサイコロを読まず、待ちのまま止まっていた。
    覚えている位置を新しい切り抜きに合わせて移すようにした。トレーが1秒以上空なら、次に現れたサイコロは新しい投擲。
  - 確定直後に揺れたサイコロを、次の人の出目としてもう一度確定していた。見えたまま同じ場所・同じ目なら同じサイコロとみなす。
  - 値が定まらないまま10秒たったら、いちばん多く読めた目で確認を求める（以前はエラーか無反応）。
  - トレーの外形は、重なりが98%を下回ったら取り直す（以前は92%で、20ピクセルほどのずれを無視していた）。認識エラー中も取り直す。
  - 認識の判断をログに残す（画像なし）: `recognition_state_changed`（候補・信頼度・通らなかった検査 `failedChecks`）、
    `recognition_candidate_discarded`、`recognition_die_ignored`（待ちのままサイコロが3秒見えている）、`recognition_tray_changed`。

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
- サイコロだけが転がっても「はっきりした動き」（動きのしきい値 0.08）にはならない（トレーの2〜3%の大きさのため）。
  動きを必須にする処理を足すと、投擲を受け付けないまま止まることがある。
- 使った実況プロンプトの版は書き換えない。研究で前の版と比べられるようにするため。
