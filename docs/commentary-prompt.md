# 実況のプロンプト仕様

実況文を作るときに LLM（Claude / OpenAI）へ送るプロンプトと、その前後の処理をまとめる。
プロンプトのバージョンは `commentary-v2`（[context.py](../apps/server/src/commentator/services/llm/context.py) の `PROMPT_VERSION`）。
設計の判断は [ADR 0005](decisions/0005-commentary-and-speech.md) にある。

## 実況の方針（v2、2026-11-22）

- 勝負を盛り上げる。逆転、追い越し、差の変化を強調する。
- **マスの移動がいちばん大きい展開を優先して話す**（ハシゴで進む・ヘビで下がるマス数。進むか下がるかは問わない）。
  候補は、今回の出目で起こり得たこと、今振った人の次の手番、次に振る人の手番。触れるのは多くても2つまで。
- ゴールに関わること（ゴールした、次にゴールできる目）は何より優先する。
- 大きな展開がないときは、これまでの試合展開（差、逆転の回数、これまでで最大の移動など）を話す。
- 起こり得たことや次の可能性は、LLM に盤面から推論させず、プログラムで計算して事実として渡す
  （[outlook.py](../apps/server/src/commentator/services/llm/outlook.py)。ゲームと同じ移動計算 `Board.move` を使う）。

v1 との違い: 上の方針をシステムプロンプトに加え、ユーザープロンプトの JSON に `thisRollOptions`・`nextForPlayer`・
`nextForNextPlayer`・`biggestSwings`・`race`・`gameFlow` を加えた。`overshotGoalAndBouncedBack` は、
実際のルール（ゴールを超える目ではその場にとどまる）に合わせて `overshotGoalStayedPut` に名前を変えた。

## 全体の流れ

1. 出目が登録される（自動認識・確認・手動入力・訂正のどれでも）。
2. ゲームの状態から、そのターンの事実（コンテキスト）を作る（`build_context`）。起こり得たことと次の可能性、勝負の状況も計算する（`analyze_turn`）。
3. システムプロンプトとユーザープロンプトを組み立てる（`build_system_prompt`・`build_user_prompt`）。
4. LLM を呼ぶ。失敗したら設定の回数だけ再試行する。
5. 返ってきた文を検査・整形する（[safety.py](../apps/server/src/commentator/services/llm/safety.py)）。使えなければテンプレート文に置き換える。
6. 画面に出し、読み上げる（プレイヤー画面では声と同時に文を出す）。

実況は**出目1回ごと**に1つ作る。画面の「ターン」（全員が1回ずつ振る単位）ごとではない。

### 実況を作るとき・作らないとき

| できごと | 実況 |
|---|---|
| 出目の登録、Redo | 新しい出目について作る |
| 最新の出目の訂正 | 訂正後の結果について作り直す |
| Undo | 画面の実況を消す。1つ前の出目について作り直すことはしない |
| 一時停止・再開・位置の訂正 | 今の実況をそのまま残す |
| 別のゲームに切り替え・サーバー起動直後 | 消す |
| 保存終了・中止 | 消す |
| 管理画面の「実況を再生成」 | 最新の出目について作り直す（`revision` が1つ増える） |
| 実況の設定がオフ | 作らない |

## 設定（管理画面「実況・読み上げの設定」、`var/commentary-settings.json`）

| 設定 | 値 | 既定値 | プロンプトへの影響 |
|---|---|---|---|
| `provider` | `fake`（テンプレート）/ `claude` / `openai` | `fake` | どの LLM に送るか。`fake` は LLM を使わずテンプレート文を返す |
| `claudeModel` | モデル名 | `claude-opus-5-5` | Claude のモデル |
| `openaiModel` | モデル名 | `gpt-5-mini` | OpenAI のモデル |
| `language` | `ja` / `en` | `ja` | プロンプト全体の言語 |
| `length` | `short` / `medium` / `long` | `medium` | 長さの指示と上限（下の表） |
| `style` | `lively` / `calm` / `kids` | `lively` | 話し方の指示 |
| `useNames` | true / false | true | プレイヤーを名前で呼ぶか、「プレイヤー1」などで呼ぶか |
| `timeoutSeconds` | 1〜60 | 10 | 1回の呼び出しの待ち時間 |
| `maxRetries` | 0〜3 | 1 | 再試行の回数 |

2026-11-22 時点の実際の設定: `claude`・`claude-opus-5-5`・`ja`・`medium`・`lively`・名前を使う・10秒・再試行1回。

### 長さ（`LENGTH_LIMITS`）

日本語は文字数、英語は単語数。「目安」はプロンプトで伝える長さ、「上限」は応答の検査で使う最大値。

| 設定 | 日本語の目安 / 上限 | 英語の目安 / 上限 |
|---|---|---|
| `short` | 40字 / 70字 | 20語 / 35語 |
| `medium` | 80字 / 130字 | 40語 / 65語 |
| `long` | 140字 / 210字 | 70語 / 105語 |

### 話し方（`_STYLE_JA`・`_STYLE_EN`）

| 設定 | 日本語の指示 |
|---|---|
| `lively` | テンポのよいスポーツ実況のように、明るく盛り上げる。 |
| `calm` | 落ち着いた解説者のように、穏やかで聞き取りやすく話す。 |
| `kids` | 小さな子どもにも分かるやさしい言葉で、楽しく話す。 |

## システムプロンプト（`build_system_prompt`）

設定だけで決まり、ターンによって変わらない。日本語の全文（`{}` は設定で入る部分）:

```text
あなたは、実物のボードゲーム「ヘビとハシゴ」を遊んでいる人たちのそばで、ターンごとに短い実況をする実況者です。勝負を盛り上げるのが役目です。
守ること:
- 与えられた JSON の事実だけを使う。数字、マス、順位を変えたり、起きていない出来事を付け足したりしない。
- 長さは日本語で{目安}字程度、多くても{上限}字以内にする。
- 出力は実況の文章だけにする。前置き、かぎかっこ、箇条書き、記号の飾り、絵文字、Markdown は使わない。
- 音声で読み上げるので、耳で聞いて分かる自然な話し言葉にする。
- プレイヤーをからかったり傷つけたりする言葉、乱暴な言葉は使わない。悪い結果でも前向きに伝える。
- {名前の指示}
話す内容の選び方:
- まず turn の結果を伝える。jump が ladder ならハシゴを登った、snake ならヘビで下がったこと、overshotGoalStayedPut が true ならゴールを超えて進めなかったことを伝える。
- ゴールに関わること（ゴールした、goalRolls にあるゴールできる目）は何より優先する。
- 次に、biggestSwings の先頭から、マスの移動がいちばん大きい展開を話す。biggestSwings は移動マス数（squares）の大きい順に並んでいる。触れるのは多くても2つまで。
  - hit_ladder / hit_snake: 今回の出目で実際にハシゴを登った / ヘビで下がった
  - missed_ladder: roll の目ならハシゴを登れた（惜しかった）
  - avoided_snake: roll の目ならヘビで下がっていた（危なかった）
  - next_ladder / next_snake: player が次の手番で roll を出すとハシゴを登る / ヘビで下がる
- race で逆転（leadChanged）や追い越し（overtook）があれば、勝負の動きとして強調する。
- biggestSwings が空で大きな展開がないときは、race と gameFlow から試合の流れ（差、逆転の回数、いつからトップか、これまでで最大の移動）を話す。
- 可能性は「6分の1」のように、耳で聞いて分かる言い方にしてよい。
話し方: {話し方の指示}
```

名前の指示:
- `useNames` が true: 「プレイヤーは JSON の名前のとおりに呼び、敬称は「さん」を付ける。」
- `useNames` が false: 「プレイヤーは JSON にある「プレイヤー1」などの呼び方だけを使う。」

英語（`language: en`）も同じ内容を英語で書いたもの（[context.py](../apps/server/src/commentator/services/llm/context.py) の `build_system_prompt` を参照）。

## ユーザープロンプト（`build_user_prompt`）

1行の依頼文のあとに、事実を JSON で付ける。

```text
このターンの状況です。実況を1つ作ってください。
{JSON}
```

英語では `The situation of this turn. Write one comment.`。

### JSON の項目（`CommentaryContext.facts()`）

**`turn`: 今回の出目（今振った人の1回分）**

| 項目 | 意味 |
|---|---|
| `turnNumber` | 画面の「ターン」の番号（全員が1回ずつ振る単位）。先攻と後攻で同じ番号になる |
| `player` | 振った人の名前（`useNames` が false なら「プレイヤー1」など） |
| `roll` | 出目（1〜6） |
| `from` | 振る前のマス（スタートは0） |
| `afterRoll` | 出目の分だけ進んだマス（ハシゴ・ヘビの前。ゴールを超える目ではその場にとどまるので `from` と同じ） |
| `final` | 最終的なマス（ハシゴ・ヘビの後） |
| `overshotGoalStayedPut` | ゴールを超える目だったため、進めずにその場にとどまったか |
| `jump` | `ladder` / `snake` / null |
| `events` | このターンのできごと（下の表） |

`events` の値:

| 値 | 条件 |
|---|---|
| `normal_move` | ゴールを超えずに進んだ |
| `goal_overshoot` | ゴールを超える目で、進めなかった |
| `large_roll` | 出目が5以上 |
| `small_roll` | 出目が2以下 |
| `ladder` / `snake` | ハシゴを登った / ヘビで下がった |
| `goal` | ちょうどゴールした |
| `near_goal` | ゴールまで6マス以内（ゴールしていない） |

**全体の状況**

| 項目 | 意味 |
|---|---|
| `goalSquare` | ゴールのマス（標準の盤は100） |
| `leader` | いちばん進んでいる人の名前。同点、または全員0マスのときは null |
| `gameFinished` | ゲームが終わったか |
| `players` | **全員**の状況（下の表）。参加順に並ぶ |
| `recentTurns` | 今回より前の出目を**全員分**、古い順に最大6回分（`RECENT_TURNS`） |

`players` の各項目:

| 項目 | 意味 |
|---|---|
| `name` | 名前 |
| `turnOrder` | 振る順番（1から） |
| `position` | 今のマス |
| `rank` | 確定した順位。ゴールするまでは null |
| `reachedGoal` | ゴールしたか |
| `laddersClimbed` / `snakesHit` | これまでにハシゴを登った回数 / ヘビで下がった回数 |

`recentTurns` の各項目: `turnNumber`・`player`・`roll`・`from`・`final`・`jump`・`overshoot`（`afterRoll` と `events` は含まない）。

**起こり得たこと・次の可能性・勝負の状況（v2 で追加、[outlook.py](../apps/server/src/commentator/services/llm/outlook.py)）**

| 項目 | 意味 |
|---|---|
| `thisRollOptions` | 今回、振る前のマスから1〜6のそれぞれが出ていたら。各項目は `roll`・`landsOn`（出目の分進んだマス）・`final`（ハシゴ・ヘビの後）・`jump`・`actual`（実際に出た目か）。ゴールを超える目には `overshootStaysPut: true` |
| `nextForPlayer` | 今振った人が次の手番で1〜6を出したとき。`player`・`position`・`ladderChances`・`snakeRisks`（各 `roll`・`from`・`to`・`squares`）・`goalRolls`（ゴールできる目）。ゴール済み・ゲーム終了時は null |
| `nextForNextPlayer` | 次に振る人について、`nextForPlayer` と同じ形。2人なら相手。今振った人と同じ人、ゴール済み、ゲーム終了時は null |
| `biggestSwings` | 上の3つから集めたハシゴ・ヘビを、移動マス数（`squares`）の大きい順に最大5件。各項目は `kind`・`player`・`roll`・`from`・`to`・`squares` |
| `race` | 今回の出目の前と後での勝負の状況（下の表） |
| `gameFlow` | ここまでの試合展開（下の表） |

`biggestSwings` の `kind`:

| 値 | 意味 |
|---|---|
| `hit_ladder` / `hit_snake` | 今回の出目で実際にハシゴを登った / ヘビで下がった |
| `missed_ladder` | 別の目（`roll`）ならハシゴを登れた |
| `avoided_snake` | 別の目（`roll`）ならヘビで下がっていた |
| `next_ladder` / `next_snake` | `player` が次の手番で `roll` を出すとハシゴを登る / ヘビで下がる |

移動マス数が同じときは、上の表の順（実際に起きたこと → 今回起こり得たこと → 次の可能性）に並べる。
ゴールは移動マス数で比べられないので `biggestSwings` には入れず、`goalRolls` と `turn.events` で伝え、システムプロンプトで最優先にしている。

`race` の項目:

| 項目 | 意味 |
|---|---|
| `leaderBefore` / `leaderAfter` | 今回の出目の前 / 後でいちばん進んでいる人（同点・全員0マスなら null） |
| `leadChanged` | トップが入れ替わったか |
| `overtook` | 今回追い越した人（スタートの0マスにいる人は数えない） |
| `fellBehind` | 今回（ヘビなどで）追い越された人 |
| `behindLeader` | 今振った人がトップから何マス後ろか（トップまたは同点なら0） |
| `leadMargin` | 今振った人が単独トップのとき、2番目との差。それ以外は null |

`gameFlow` の項目:

| 項目 | 意味 |
|---|---|
| `rollsSoFar` | これまでの出目の回数（今回を含む） |
| `leadChanges` | トップが入れ替わった回数（同点は入れ替わりに数えない） |
| `leaderSinceTurn` | 今のトップがトップになったターンの番号（同点なら null） |
| `biggestJumpSoFar` | これまでで最大のハシゴ・ヘビの移動（`player`・`turnNumber`・`jump`・`from`・`to`・`squares`） |

`race` の位置は、最新の出目では実際の位置（位置の訂正を含む）を使う。`gameFlow` は出目の記録から計算するので、位置の訂正は反映されない。

### 何が送られるか（2人の場合）

- **先攻**: 同じターンではまだ誰も振っていないので、`recentTurns` は前のターンまでの両者の出目（直前3ターン分）。相手の今のマスは `players` で分かる。
- **後攻**: `recentTurns` の最後が、同じターンの先攻の出目。

相手との比較や流れは、`race`・`gameFlow`・`nextForNextPlayer` とシステムプロンプトの指示で話すようにしている（v2）。

### 例（架空のゲーム。標準の盤、2人、3ターン目の先攻がハシゴを登った）

出目は あおい 3、はると 5、あおい 2、はると 6、あおい 4（5→9、ハシゴで31）。v2 で加えた部分だけを示す
（`thisRollOptions` は省略。実際の送信では JSON は1項目ずつ改行して字下げ1で書かれる）。

```json
{
 "nextForPlayer": {"player": "あおい", "position": 31,
  "ladderChances": [{"roll": 5, "from": 36, "to": 44, "squares": 8}], "snakeRisks": [], "goalRolls": []},
 "nextForNextPlayer": {"player": "はると", "position": 11,
  "ladderChances": [], "snakeRisks": [{"roll": 5, "from": 16, "to": 6, "squares": 10}], "goalRolls": []},
 "biggestSwings": [
  {"kind": "hit_ladder", "player": "あおい", "roll": 4, "from": 9, "to": 31, "squares": 22},
  {"kind": "next_snake", "player": "はると", "roll": 5, "from": 16, "to": 6, "squares": 10},
  {"kind": "next_ladder", "player": "あおい", "roll": 5, "from": 36, "to": 44, "squares": 8}
 ],
 "race": {"leaderBefore": "はると", "leaderAfter": "あおい", "leadChanged": true,
  "overtook": ["はると"], "fellBehind": [], "behindLeader": 0, "leadMargin": 20},
 "gameFlow": {"rollsSoFar": 5, "leadChanges": 2, "leaderSinceTurn": 3,
  "biggestJumpSoFar": {"player": "あおい", "turnNumber": 3, "jump": "ladder", "from": 9, "to": 31, "squares": 22}}
}
```

このときの Claude（claude-opus-5-5）の実況の例（2026-11-22）:
「あおいさん4を出して9マスへ、そこからハシゴで一気に31マス、22マスの大ジャンプです。はるとさんを抜いてトップに浮上、その差は20マス。はるとさん、次は5だけ気をつけて、反撃に期待しましょう。」

## LLM の呼び出し

| | Claude（[providers.py](../apps/server/src/commentator/services/llm/providers.py)） | OpenAI |
|---|---|---|
| API | Messages API（`system` にシステムプロンプト、`messages` の user にユーザープロンプト） | Responses API（`instructions` と `input`） |
| 出力の上限 | `max_tokens` 4000 | `max_output_tokens` 4000 |
| その他 | Claude 5 世代などでは `effort: low`（短い実況なので考える量を減らし、速くする）。一部のモデルでは、断られたときにサーバー側で別のモデルに切り替える設定を付ける | なし |
| 鍵 | `ANTHROPIC_API_KEY`（`var/secrets.env`） | `OPENAI_API_KEY` |

- 1回の待ち時間は `timeoutSeconds`。時間切れ・接続失敗・利用上限・HTTP 5xx は、`maxRetries` 回まで再試行する（間隔は0.5秒、1秒、2秒…）。
- 認証の失敗や、モデルが応答を断ったときは再試行しない。
- 待っている間に新しい出目が入ったら、古い要求の再試行はやめ、届いた応答も捨てる。

## 応答の検査と整形（[safety.py](../apps/server/src/commentator/services/llm/safety.py)）

1. Markdown の記号（`*`、`_`、バッククォート、`#`、`>`、`[`、`]`、行頭の `-`・`・`）、改行、前後のかぎかっこ・引用符を取り除く。
2. 空なら使わない（`empty`）。
3. 禁止語（「バカ」「死ね」など、`BLOCKED_WORDS`）を含むなら使わない（`blocked_word`）。
4. 上限を超えたら、上限内で最後の文末（。！？!?）で切る。切れる位置がなければ使わない（`too_long`）。
5. 出力の上限で途中で切れた応答は、文末で切り直していなければ使わない（`truncated`）。

使えなかったとき、または LLM を呼べなかったときは、事実だけで作るテンプレート文に置き換える（`template_commentary`）。
例: 「あおいさん、出目は4！5マス目から9マス目へ進みます。ハシゴを登って一気に31マス目！現在トップを走っています。」
管理画面では「代替文」と理由が表示される。

## 記録と確認のしかた

- **管理画面「実況のプロンプト」**: 今の設定のシステムプロンプトと、最新の出目のユーザープロンプト（`GET /api/commentary/prompt`）。
- **管理画面「実況の記録」**: 実際に送ったプロンプト、応答の全文、最終的に使った文、所要時間、試行回数、エラー（最新20件）。
  SQLite の `commentary_requests` テーブルに保存される（`GET /api/commentary/requests`）。
- 通常のログ（`var/log/commentator.jsonl`）にはプロンプトや実況の文を書かない（[logging.md](operations/logging.md)）。

## プロンプトを変えるとき

- システムプロンプトは `build_system_prompt`、依頼文は `build_user_prompt`、JSON の中身は `CommentaryContext.facts()` と `build_context`、
  起こり得たこと・次の可能性・勝負の状況は `outlook.py` の `analyze_turn`（`MAX_SWINGS` で件数、`SWING_KINDS` で同じ大きさのときの順）。
- 内容を変えたら `PROMPT_VERSION` を上げる（例: `commentary-v2`）。記録に残るので、どの版で作った実況か後から分かる。
- 長さの目安・上限は `LENGTH_LIMITS`、話し方は `_STYLE_JA`・`_STYLE_EN`。
- テストは `apps/server/tests/test_commentary.py` と `test_commentary_outlook.py`。
