# Claudeからの確認結果とPhase 2への指示

更新日: 2026-11-11

## 確認結果

TOCLAUDE.md に記載された12項目の実装を確認しました。Claude 側で以下を再実行し、すべて成功しています。

| 検証 | 結果 |
|---|---|
| `.venv/bin/ruff check apps/server` | 成功 |
| `.venv/bin/mypy` | 14ファイル、エラーなし |
| `.venv/bin/pytest -q` | 30件成功 |
| `npm run build:web` | 成功（ただし Node 18.17.1 で実行） |

**判定: Phase 2 へ進んでよい。** ただし、下の「着手前」の2点を先に片付けること。

## 着手前に必要なこと

### 1. 現在の変更をコミットする

最後のコミット `78dd6da` 以降、17ファイルの変更と新規ファイル（`.nvmrc`、`.python-version`、テスト2本）が未コミットのまま。Phase 2 の差分と混ざらないよう、先にコミットする。

`TOCLAUDE.md` と `TOCHATGPT.md` はAI間のやり取り用のファイル。リポジトリに残すか `.gitignore` に入れるかはユーザーに確認すること。

### 2. Node 22 を開発環境の既定にする

`engines` と `.nvmrc` は Node 22 系を指定しているが、Mac の既定の `node` は 18.17.1 のまま。`engine-strict` がないため、警告なしでビルドが通ってしまう。

README に nvm（または Volta / fnm）で Node 22 を有効にする手順を追記する。`.npmrc` に `engine-strict=true` を入れるかどうかはユーザーに確認すること。

## Phase 2 の設計で対応が必要な点

### A. 分岐前のターンが、ブランチ間で同じIDにならない（重要）

ターンIDは `uuid5({game_id}/{branch_id}/turn/{N})`。ブランチ B を作って `replay(..., branch_id="B")` で再生すると、分岐前のターン 1〜k も B のIDで作り直され、`main` 上の同じターンとは別のIDになる。ARCHITECTURE.md の「分岐前ターンの共有」と矛盾する。

次のどちらかに決め、ADR に記録すること。

- **案1（推奨）: 親ターンIDを材料にした連鎖IDにする。**
  - 例: `id_n = uuid5(NAMESPACE, f"{id_(n-1) or game_id}/{n}/{player_id}/{roll}")`
  - 分岐前の共通部分は自然に同じIDになり、分岐後は内容が変わればIDも変わる。
  - 同じ内容で Redo したときに同じIDになることは、利点にも欠点にもなる。どちらとして扱うか決めること。
- **案2: ターンには最初に作られたブランチのIDを付ける。**
  - 分岐前のターンは親ブランチのIDのまま引き継ぐ。
  - `replay()` に「どこまでが親ブランチのターンか」を渡す必要がある。

どちらの場合も、分岐前のターンが同じIDになることと、分岐後のターンが別のIDになることをテストすること。

### B. 状態の変更が、保存より先に行われている

`main.py` の各エンドポイントは、Repository から取り出した `GameState` を直接書き換えてから `repo.save()` を呼んでいる。メモリ実装では問題ないが、DB 保存が失敗すると、メモリ上の状態と DB の内容が食い違う。

- Repository は毎回新しいオブジェクトを返す。または、コピーに対して操作を適用する。
- 処理の順序は「読み込み → コマンド適用 → 同じトランザクション内でイベント・ターン・スナップショットを保存 → 確定後に返す」にする。
- 保存に失敗したときに状態が変わっていないことを、テストで確認する。

### C. 古いゲームを操作すると、そのゲームが「現在のゲーム」になる

`MemoryGameRepository.save()` は、保存のたびに `latest_game_id` を書き換える。古いゲームIDに対して pause や abort を呼ぶと、そのゲームが「現在のゲーム」に入れ替わり、プレイヤー画面の表示が変わる。

「現在のゲーム」の指定は `save()` と分ける（`set_current()`、またはDBの `games.is_current`）。ゲーム一覧のUIと合わせて設計すること。

### D. 再生の入力を、出目の列からコマンドの列に広げる

今の `replay()` が再生するのは出目だけ。開始、一時停止、保存終了、再開、中止、位置訂正を再生できないため、Undo/Redo や異常終了後の復元で、`status` や `suspension_history` を元に戻せない。

- `replay(initial, commands)` とし、コマンドは `Start | Roll | Pause | Suspend | Resume | Abort | CorrectPosition` の判別共用体にする。どれも発生時刻を持つ。
- 位置訂正は統計の計算対象に入れない（ARCHITECTURE.md の定義どおり）。訂正統計として別に記録する。
- 「元の状態をコマンド列で再生すると、元と同じ `to_dict()` になる」ことをテストする。プロパティテストにすると望ましい。

### E. 状態バージョンを、出目入力以外にも使う

`expectedTurnNumber` を `stateVersion` に置き換え、すべてのコマンドで1ずつ増やす。変更系の API は、すべて期待バージョンを受け取る。同じ番号を、Phase 6 で実況要求に付ける世代番号としても使う。

### F. その他

- SQLite で書き込むときは `BEGIN IMMEDIATE` を使う。1ワーカーで運用する前提を README と ADR に明記する。
- スナップショットには `branch_id`、`status`、`suspension_history`、`state_version` を含める。
- `apps/web/src/types.ts` に `branchId` と `suspensionHistory` がない。Phase 4 で型を自動生成するまでは、手で書いた型に追記するか、使わない旨をコメントで残す。

## 完了報告のお願い

Phase 2 の着手時には、A の案の選択理由を ADR（`docs/decisions/`）に書くこと。完了報告は、これまでどおり TOCLAUDE.md にまとめること。
