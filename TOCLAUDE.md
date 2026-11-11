# Claudeレビューへの修正報告

更新日: 2026-11-11

この文書は、Phase 2着手前のレビュー12項目に対する実装変更と検証結果をまとめたものです。行番号は変更でずれるため、ファイルとシンボル名で参照します。

## 1. ターンIDのブランチ対応

対象: [game.py](apps/server/src/commentator/domain/game.py) の `GameState.branch_id`、`roll()`、`replay()`

- 初期ブランチIDは `main`。
- ターンIDはUUID v5で生成し、名前の材料は `{game_id}/{branch_id}/turn/{turn_number}`。
- 同じゲーム・ブランチ・ターン番号での再生は同じIDを返す。
- 訂正履歴を別ブランチIDで再生すると、同じターン番号でも別IDになる。
- `replay(initial, rolls, started_at=..., branch_id=...)` でブランチを指定可能。初期状態はコピーし、元の状態を変更しない。
- レスポンスには `branchId` を追加。

確認: `test_branch_ids_and_deterministic_replay` で、訂正前後のIDの相違、同一ブランチ再生の一致、初期状態の不変を検証。

Phase 2で必要なこと: 訂正時のブランチID発行・保存、親子関係、分岐前ターンの共有、無効化された履歴の管理。現在は別ブランチIDを渡す仕組みまでであり、異なる出目列に同じブランチIDを再利用すること自体をドメインで検出する機能はない。

## 2. maximumPositionの定義

対象: [game.py](apps/server/src/commentator/domain/game.py) の `compute_statistics()`

ユーザー合意により、ヘビの頭へ到達したマスも最高到達位置に含める。

計算は、各ターンの「移動前位置・通常移動後位置・最終位置」の最大値を履歴全体で集計する。例えば `92 → 93 → 73` なら最高到達位置は93。

確認: `test_snake_head_counts_as_maximum_position`。定義を [ARCHITECTURE.md](docs/ARCHITECTURE.md) に記載。

## 3. 保存終了からの再開と中断記録

対象: [game.py](apps/server/src/commentator/domain/game.py) の `resume()`、`suspend()`

- 設計を実装に合わせ、`SUSPENDED → IN_PROGRESS` に統一。
- 再開時に開始時刻は変更しない。
- `suspended_at` は最後の中断時刻として保持し、再開時に消さない。
- `suspension_history` にすべての中断時刻を追記。
- レスポンスは `suspendedAt` と `suspensionHistory` を返す。

確認: `test_resume_keeps_all_suspension_times` で複数回の中断・再開後も記録が残ることを検証。

注: 再開時刻や停止時間の集計、操作イベントとしての永続化はPhase 2の対象。

## 4. イベントの型と超過時の扱い

対象: [game.py](apps/server/src/commentator/domain/game.py) の `EventType`、`TurnRecord.events`、`_events_for()`

`EventType(StrEnum)` を導入し、イベント一覧を `tuple[EventType, ...]` に変更した。JSONでは従来どおり文字列値になる。

分類規則:

- 通常は `normal_move`、超過時は代わりに `goal_overshoot`。
- 出目5・6には `large_roll`、1・2には `small_roll`。超過時にも保持。
- ヘビ・ハシゴを適用した場合は、それぞれ `snake`・`ladder`。
- 最終位置が100なら `goal`。
- ゴール未到達かつ最終位置が手前6マス以内なら `near_goal`。超過でも付与し、ゴール時には付与しない。

確認: パラメーター化した `test_events` の6ケースで通常移動、大小、超過、ヘビ、ハシゴ、ゴールを検証。

注: 首位交代や訂正など、まだ未実装のイベント種別は今後追加する。

## 5. アプリ状態の分離とRepository差し込み

対象: [main.py](apps/server/src/commentator/main.py)、[test_api.py](apps/server/tests/test_api.py)

- `create_app(board=None, repository=None)` を導入。
- `GameRepository` Protocolに `get()`、`save()`、`current()` を定義。
- 初期実装として `MemoryGameRepository` を追加。
- 盤面、Repository、排他ロックをアプリ生成ごとに用意。モジュール共有のゲーム辞書を廃止。
- 本番エントリーポイントは `app = create_app()`。
- APIテストはfixtureで毎回新しいアプリを作成する。
- 状態変更後に `repo.save(game)` を呼ぶ。

確認: `test_factory_isolation_validation_and_invalid_transitions` でRepositoryの差し込みと別アプリの空状態を検証。並行二重送信テストも継続して成功。

注: 現在のRepositoryは可変なGameStateを返すメモリ実装。DBトランザクション、失敗時のロールバック、複数プロセス間の競合制御はPhase 2で設計・実装する。

## 6. APIフィールド名の統一

対象: [schemas.py](apps/server/src/commentator/api/schemas.py)

- リクエスト共通モデルに `alias_generator=to_camel` を設定。
- 外部JSONはcamelCase、Python内部はsnake_caseに統一。
- 例: `displayName`、`spokenName`、`expectedTurnNumber`。
- テスト入力も `spoken_name` から `spokenName` へ変更。
- レスポンスは従来からcamelCaseであり、この規約を維持。

確認: API経由で `spokenName` を入力し、レスポンスへ反映されることを検証。

## 7. mypyの対象にテストを追加

対象: [pyproject.toml](pyproject.toml)、[tests](apps/server/tests)

- `tool.mypy.files` にsrcとtestsを指定。
- `current_player` や `jump` のOptional値は、テストで明示的にNoneでないことを確認してから参照。
- HTTPテストのヘルパーは、任意の `**dict[str, object]` をhttpxへ渡す方式をやめ、JSON入力を明示した形へ変更。
- READMEの実行コマンドは、設定された全対象を検査する `.venv/bin/mypy` に更新。

検証時点では14ファイルを検査し、型エラーなし。

## 8. 不足していたテスト

主な追加先: [test_review_regressions.py](apps/server/tests/test_review_regressions.py)

- ブランチごとのターンIDと決定的再生
- ヘビの頭を含む最高到達位置
- 複数回の保存終了・再開と中断履歴
- イベント分類（6ケース）
- ゴール済みと未ゴールが混在する場合の同順位
- READYからの再開など不正な状態遷移の409
- READYからの中止
- 中止後の開始拒否
- 一時停止中の出目入力拒否
- 空白名の拒否と名前の前後空白除去
- アプリごとの状態分離とRepository差し込み

既存の二重送信、出目訂正を想定した再生、盤面到達可能性などのテストも維持。

## 9. jump_by_startの事前構築

対象: [board.py](apps/server/src/commentator/domain/board.py)

- 呼び出しごとのdict構築を廃止。
- 盤面構築時に一度生成し、読み取り専用の `MappingProxyType` として保持。
- 移動、盤面の到達可能性検証、最短手数計算で同じマッピングを参照。
- 不変なBoardの `__deepcopy__()` は自身を返し、再生時に読み取り専用マッピングをコピーしようとする問題を回避。

## 10. 入力検証・依存関係・型

- [pyproject.toml](pyproject.toml) に直接依存 `pydantic>=2.9,<3` を追加。
- リクエストモデルに `str_strip_whitespace=True` を指定。
- 名前・色の必須文字列に加え、任意の表示名・読み方も、指定された場合は空文字を拒否。
- [main.py](apps/server/src/commentator/main.py) の `_apply_action` を `Callable[[], object]` で型付け。

## 11. 実行バージョンの指定

- [.nvmrc](.nvmrc): Node 22系
- [.python-version](.python-version): Python 3.13系
- [package.json](package.json): `node >=22 <23`
- [pyproject.toml](pyproject.toml): `requires-python >=3.13,<3.14`
- mypyの対象Pythonも3.13に変更。
- package-lock.jsonを同期。

これはメジャー／マイナー系列の指定であり、全依存関係や実行環境のパッチ版まで完全固定したものではない。検証環境はPython 3.13.3、Node 22.23.4。Node 22は一時実行で検証しており、MacのデフォルトNode自体は変更していない。

## 12. READMEと設計・ToDoの更新

[README.md](README.md) の冒頭を「ゲームエンジン、手動入力API、管理者・プレイヤー画面の初期実装が動作する段階」に更新。Python・Nodeの設定方法と、テストを含む型検査コマンドを追記。

[ARCHITECTURE.md](docs/ARCHITECTURE.md) へブランチID、最高到達位置、再開・中断履歴、イベント分類、API命名規約、アプリ生成方針を反映。

[TODO.md](TODO.md) へ完了項目とPhase 2の残作業を反映。

## 前回レビュー分の対応

今回の12項目の前提として、以下も実装済み。

- `compute_statistics(turns)` による履歴からの統計再計算。
- 時刻付き出目列と初期状態による `replay()`。
- UI送信中の無効化と送信ロック、APIの `expectedTurnNumber` チェックと排他制御。
- ハシゴ経由を含む `winningRolls`。75マスでは `[5]`。
- ゴールへ到達できないマスがある盤面の構築時拒否。
- 正常終了・中止後の「新しいゲーム」ボタン。

## 検証結果

直前の実装検証で以下が成功。

| 検証 | 結果 |
|---|---|
| `.venv/bin/ruff check apps/server` | 成功 |
| `.venv/bin/mypy` | src・tests計14ファイルでエラーなし |
| `.venv/bin/pytest -q` | 30件成功 |
| Node 22での `npm run build:web` | TypeScript型検査・Viteビルド成功 |

この報告書追加時には、上記テストを再実行していない。

## Phase 2へ残した範囲

- SQLite保存、スナップショット、異常終了後の復元。
- 訂正・Undo/Redoの操作と履歴ブランチの永続管理。
- 一時停止・再開・中止・位置訂正も含めた操作イベント再生。
- 全操作を対象にした状態バージョン。現在の期待番号チェックは出目入力のみ。
- ゲーム一覧と保存終了したゲームを開く画面。
- ポーリングからWebSocketへの切り替え。
- DBトランザクションと永続化失敗時の整合性。

現在の「保存終了」はメモリ上の状態遷移で、プロセス終了をまたぐ保存はまだ行わない。APIは単一プロセス・1ワーカーで運用する前提。
