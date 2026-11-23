# 実況のプロンプト仕様

実況文を作るときに LLM（Claude / OpenAI）へ送るプロンプトと、その前後の処理をまとめる。
プロンプトの版は設定で切り替えられる（既定は最新の `commentary-v5`）。各版は [prompts/](../apps/server/src/commentator/services/llm/prompts/) にある。
この文書は v3 を中心に書き、v4 で加えたことは「v4 で加えたこと」、v5 で変えたことは「v5 で変えたこと」にまとめる。
設計の判断は [ADR 0005](decisions/0005-commentary-and-speech.md) にある。

## 実況の方針（v3、2026-11-22）

- 勝負を盛り上げる。ただし**中立**で、どのプレイヤーにも肩入れしない。ある人の危険は別の人の望みとして、両方の立場から伝える。
- **感情を込める**。実際に起きたことの大きさに合わせて、最初の一言を大歓声から大きな落胆まで変える。
- **勝負への影響がいちばん大きい展開を優先して話す**。影響は「その展開で、勝つ確率がどれだけ変わるか」で測る。
  ゴール前の短いヘビや、追う側の逆転につながるハシゴも、マス数だけで測るより正しく重みが付く。触れるのは多くても2つまで。
- 勝負の局面（接戦・優勢・勝利が見えてきた）を伝える。**勝つ確率は数字で言わず、言葉だけ**で伝える。
- サイコロの目は選べないので、「慎重に」「気をつけて」とは言わない。
- 起こり得たこと・次の可能性・勝つ確率は、LLM に推論させずにプログラムで計算して事実として渡す
  （[outlook.py](../apps/server/src/commentator/services/llm/outlook.py)、[winning.py](../apps/server/src/commentator/services/llm/winning.py)。
  ゲームと同じ移動計算 `Board.move` を使う）。

### 版の履歴

| 版 | 変更 |
|---|---|
| v1 | ターンの事実・全員の位置・直前6回の出目を渡し、事実だけで実況する |
| v2 | 起こり得たこと・次の可能性・勝負の状況を加え、移動マス数の大きい展開を優先する。`overshotGoalAndBouncedBack` を `overshotGoalStayedPut` に改名（ゴールを超える目ではその場にとどまるルールに合わせた） |
| v3 | 1ゲーム通しのレビューを受けて変更。勝つ確率で影響を測る、反応の強さ（`reaction`）、局面（`phase`）、直前の出目（`previousRoll`）、中立の立場、数字を言わない、目を選べるような言い方をしない。`thisRollOptions` は `biggestSwings` と重なるので削除 |
| v4 | v3 で1ゲーム遊んだレビューを受けて変更。惜しい・危ないにも感情を込める（`emotion`）、直前の実況5つを渡して繰り返さない・予告の結果につなげる（`previousComments`）、出目と移動を毎回言わない、序盤は「始まったばかり」（`opening`）、続くチャンス（`stillInReach`）、根拠のない見通しを言わない |

### 版の切り替え（2026-11-22）

- 管理画面「実況・読み上げの設定」の「プロンプトの版」で選ぶ。設定の `promptVersion`（`var/commentary-settings.json`）に保存される。
- 次の実況から、選んだ版のシステムプロンプトと事実（JSON）で作る。実況の記録には使った版が残る。
- 各版は使ったあとは変えない。変えるときは新しい版を作る（`prompts/vN.py` を足し、`prompts/__init__.py` の `VERSIONS` と
  `settings.py` の `PROMPT_VERSIONS` に加える）。古い版と比べられるようにするため。

| 版 | システムプロンプト | 加える事実 |
|---|---|---|
| `commentary-v1` | [prompts/v1.py](../apps/server/src/commentator/services/llm/prompts/v1.py) | なし（`overshotGoalAndBouncedBack` の名前を使う） |
| `commentary-v2` | [prompts/v2.py](../apps/server/src/commentator/services/llm/prompts/v2.py) | [outlook_v2.py](../apps/server/src/commentator/services/llm/outlook_v2.py)（`thisRollOptions`、マス数順の `biggestSwings`、`race`、`gameFlow`） |
| `commentary-v3` | [prompts/v3.py](../apps/server/src/commentator/services/llm/prompts/v3.py) | [outlook.py](../apps/server/src/commentator/services/llm/outlook.py)（この文書で説明するもの） |
| `commentary-v4` | [prompts/v4.py](../apps/server/src/commentator/services/llm/prompts/v4.py) | [outlook_v4.py](../apps/server/src/commentator/services/llm/outlook_v4.py)（v3 の事実に、下の「v4 で加えたこと」を足す）と直前の実況 |
| `commentary-v5` | [prompts/v5.py](../apps/server/src/commentator/services/llm/prompts/v5.py)（v4 と同じ） | [outlook_v5.py](../apps/server/src/commentator/services/llm/outlook_v5.py)（v4 の事実。`emotion` の決め方だけ下の「v5 で変えたこと」のとおり）と直前の実況 |

比較の仕組みでも `--version commentary-v2` のように版を選べる。

## v4 で加えたこと（2026-11-22）

v3 で1ゲーム遊んだ感想: 全体に事実を言うだけで感情がない、毎回「○を出して△から□へ」で始まる、同じことを繰り返す、
前に予告したハシゴを「実は」と言う、最初の一投を「接戦」と言う、根拠なく「まだまだここから」と言う。

| 項目 | 内容 |
|---|---|
| `emotion` | `reaction` の代わり。`kind`（`joy` 喜び・`disappointment` 落胆・`so_close` 惜しい・`relief` ひやり・`calm`）、`strength`（`huge`・`big`・`mild`）、`about`（`actual` か、きっかけになった `missed_goal`・`missed_ladder`・`avoided_snake`）。惜しい・ひやりは、その別の目の影響が `decisive`・`big` か、ハシゴ・ヘビが15マス以上のときだけ（ゴールを逃したときは常に `big` 以上）。実際の結果と比べて強いほうを使う |
| `race.phase` | 全員が2回ずつ振るまでは `opening`（始まったばかり。接戦とは言わない） |
| `stillInReach` | `next_ladder`・`next_snake` に付く。今回逃した（避けた）ハシゴ・ヘビに、次の手番でも届く（チャンス・危険が続いている） |
| `previousComments` | このゲームで直前に出した実況を古い順に最大5つ。1ターンにつき最後に出したもの。今の出目の実況と、Undo で取り消したターンの実況は含めない |

システムプロンプトでは、感嘆の言葉の例を感情ごとに示し、`previousComments` で使った感嘆・決まり文句（「さっきお話しした」「〜を願うしかない」など）を使わないこと、
予告の結果が大きな展開ならつなぎ方を変えながら話すこと、出目と移動は意味があるときだけ言うこと、
追う人の望みは根拠と一緒に、その人の立場からの願いとして中立に言うこと（「○○さんとしては、□以外を願うしかない！」）を指示する。

比較の仕組みでは、v4 は出目の順に作り、作った実況を次の出目の `previousComments` に使う（実際のゲームと同じ）。

## v5 で変えたこと（2026-11-23）

v4 で遊んだ感想: 「危なーい！4なら49のヘビで11まで落ちていたところ、出たのは待望の6！51のハシゴで67まで」のように、
実際にハシゴを登ったのに、一言目が「ひやり」になった。避けたヘビ（38マス、影響 decisive）が、登ったハシゴ（16マス、big）より
強いと判定され、`emotion` が relief になっていた。

- ハシゴ・ヘビ・ゴールが実際に起きた出目では、`emotion` は実際の結果のもの（joy / disappointment）にする。
  「惜しい」「ひやり」は、ジャンプのない移動のときだけ使う。
- 避けたヘビなどは `biggestSwings` に残るので、二言目以降で話せる。
- システムプロンプトは v4 と同じ。v4 は使ったので変えず、新しい版にした。

## 全体の流れ

1. 出目が登録される（自動認識・確認・手動入力・訂正のどれでも）。
2. ゲームの状態から、そのターンの事実（コンテキスト）を作る（`build_context`）。起こり得たことと次の可能性、勝つ確率、勝負の状況も計算する（`analyze_turn`）。
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
| `fastMode` | true / false | true | Claude の高速モード（`speed: fast`）。今は Claude Opus 5.5 だけ。料金は2倍。2026-11-23 のゲームで平均 4.8 秒 → 2.0 秒 |
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
あなたは、実物のボードゲーム「ヘビとハシゴ」を遊んでいる人たちのそばで、ターンごとに短い実況をする実況者です。勝負を盛り上げるのが役目です。どのプレイヤーにも肩入れせず、中立に実況します。
守ること:
- 与えられた JSON の事実だけを使う。数字、マス、順位を変えたり、起きていない出来事を付け足したりしない。
- 長さは日本語で{目安}字程度、多くても{上限}字以内にする。
- 出力は実況の文章だけにする。前置き、かぎかっこ、箇条書き、記号の飾り、絵文字、Markdown は使わない。
- 音声で読み上げるので、耳で聞いて分かる自然な話し言葉にする。
- プレイヤーをからかったり傷つけたりする言葉、乱暴な言葉は使わない。悪い結果でも前向きに伝える。
- {名前の指示}
- 勝つ見込み（winChances の percent など）は数字やパーセントで言わない。「勝利が見えてきた」「まだまだ分からない」のような言葉で伝える。
- サイコロの目は選べない。「慎重に」「気をつけて」のような、プレイヤーが目を選べるかのような言い方はしない。
話す内容の組み立て:
- 最初の一言は reaction に合わせた感情にする。huge_cheer は大歓声、cheer は歓声、mild_cheer は軽い喜び、mild_groan は軽い残念さ、groan は落胆、huge_groan は大きな落胆。淡々と事実だけを述べない。calm のときは感情の言葉や前置きを足さず、結果から話し始める。
- 次に turn の結果を伝える。jump が ladder ならハシゴを登った、snake ならヘビで下がったこと、overshotGoalStayedPut が true ならゴールを超えて進めなかったことを伝える。
- 続いて biggestSwings の先頭から話す。biggestSwings は勝負への影響（impact）の大きい順に並んでいる。impact は decisive、big、notable、small の順に大きい。触れるのは多くても2つまで。
  - reached_goal / hit_ladder / hit_snake: 今回の出目で実際にゴールした / ハシゴを登った / ヘビで下がった
  - missed_goal / missed_ladder: roll の目ならゴールできた / ハシゴを登れた（惜しかった）
  - avoided_snake: roll の目ならヘビで下がっていた（危なかった）
  - next_goal / next_ladder / next_snake: player が次の手番で roll を出すとゴール / ハシゴを登る / ヘビで下がる
- ある人にとっての危険は、ほかの人にとっての望みでもある。片方を応援せず、「○○さんにとっては□が出れば望みがつながる」のように、それぞれの立場を中立に伝える。
- race.phase で勝負の局面を伝える。nearly_decided は勝利が見えてきた、pulling_away は優勢、close は接戦。phaseBefore から変わったときは強調する。逆転（leadChanged）や追い越し（overtook）も勝負の動きとして強調する。
- previousRoll（直前の出目）で大きなことがあれば、今回とつなげて語ってよい（例: 直前に相手がヘビで下がったあとに差が開いた）。
- biggestSwings に small 以外の展開がなければ、race と gameFlow から試合の流れ（差、逆転の回数、いつからトップか、これまでで最大の移動）を話す。
- サイコロの目の可能性は「6分の1」のように言ってよい。
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

英語では `The situation of this turn. Write one comment.`。JSON は1項目ずつ改行して字下げ1で書かれる。

### 今回の出目と全体の状況

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

| 項目 | 意味 |
|---|---|
| `goalSquare` | ゴールのマス（標準の盤は100） |
| `leader` | いちばん進んでいる人の名前。同点、または全員0マスのときは null |
| `gameFinished` | ゲームが終わったか |
| `players` | **全員**の状況。参加順に並ぶ。各項目は `name`・`turnOrder`（振る順番）・`position`・`rank`（確定した順位。ゴールするまでは null）・`reachedGoal`・`laddersClimbed`・`snakesHit` |
| `recentTurns` | 今回より前の出目を**全員分**、古い順に最大6回分（`RECENT_TURNS`）。各項目は `turnNumber`・`player`・`roll`・`from`・`final`・`jump`・`overshoot` |

2人の場合、先攻の出目では `recentTurns` は前のターンまでの両者の出目（直前3ターン分）、後攻の出目では最後が同じターンの先攻の出目になる。

### 起こり得たこと・次の可能性・勝負の状況（[outlook.py](../apps/server/src/commentator/services/llm/outlook.py)）

| 項目 | 意味 |
|---|---|
| `reaction` | 今回の結果への反応の強さ（下の「反応の強さ」） |
| `nextForPlayer` | 今振った人が次の手番で1〜6を出したとき。`player`・`position`・`ladderChances`・`snakeRisks`（各 `roll`・`from`・`to`・`squares`）・`goalRolls`（ゴールできる目）。ゴール・ゲーム終了時は null |
| `nextForNextPlayer` | 次に振る人について、`nextForPlayer` と同じ形。2人なら相手。ゲーム終了時は null |
| `biggestSwings` | 起こり得た展開を、勝負への影響の大きい順に最大5件（下の表） |
| `race` | 今回の出目の前と後での勝負の状況（下の表） |
| `previousRoll` | 直前の出目（`player`・`roll`・`from`・`final`・`jump`・`reaction`）。最初の出目では null |
| `gameFlow` | ここまでの試合展開（下の表） |

**`biggestSwings` の各項目**: `kind`・`player`・`roll`・`from`・`to`・`squares`（ハシゴ・ヘビの移動マス数。ゴールは null）・`impact`。
集める対象は、今回の出目で実際に起きたこと・別の目なら起きたこと（ゴール・ハシゴ・ヘビ）と、`nextForPlayer`・`nextForNextPlayer` のゴール・ハシゴ・ヘビ。

| `kind` | 意味 | 影響（勝つ確率の変化）の測り方 |
|---|---|---|
| `reached_goal` / `hit_ladder` / `hit_snake` | 今回の出目で実際にゴールした / ハシゴを登った / ヘビで下がった | 振る前と後での、振った人の勝つ確率の差 |
| `missed_goal` / `missed_ladder` | 別の目（`roll`）ならゴールできた / ハシゴを登れた | その目だった場合と、実際の結果との差 |
| `avoided_snake` | 別の目（`roll`）ならヘビで下がっていた | 同上 |
| `next_goal` / `next_ladder` / `next_snake` | `player` が次の手番で `roll` を出すとゴール / ハシゴを登る / ヘビで下がる | その目が出た場合と、今との差 |

`impact` は勝つ確率の変化の大きさ（`IMPACT_LEVELS`）:

| 値 | 変化 |
|---|---|
| `decisive` | 25ポイント以上 |
| `big` | 12ポイント以上 |
| `notable` | 5ポイント以上 |
| `small` | 5ポイント未満 |

並び順は、`impact` → 移動マス数（ゴールは最大扱い）→ `kind`（上の表の順。実際に起きたこと → 今回起こり得たこと → 次の可能性）。

**反応の強さ（`reaction`）**: 次の2つのうち強いほう（`reaction_level`）。ゴールしたときは必ず `huge_cheer`。

| 値 | 勝つ確率の変化（振った人） | ハシゴ・ヘビの大きさ（移動マス数 ÷ ゴールのマス） |
|---|---|---|
| `huge_cheer` / `huge_groan` | +20 / −20ポイント以上 | ハシゴ / ヘビで 0.30 以上（標準の盤で30マス以上） |
| `cheer` / `groan` | +8 / −8ポイント以上 | 0.15 以上（15マス以上） |
| `mild_cheer` / `mild_groan` | — | それより小さいハシゴ / ヘビ |
| `calm` | 上のどれにも当たらない | ハシゴ・ヘビなし |

序盤の長いハシゴは勝つ確率をあまり変えないが、その場では大きな出来事なので、大きさでも反応を強める（レビューの指摘）。

**`race` の項目**:

| 項目 | 意味 |
|---|---|
| `leaderBefore` / `leaderAfter` | 今回の出目の前 / 後でいちばん進んでいる人（同点・全員0マスなら null） |
| `leadChanged` | トップが入れ替わったか |
| `overtook` | 今回追い越した人（スタートの0マスにいる人は数えない） |
| `fellBehind` | 今回（ヘビなどで）追い越された人 |
| `behindLeader` | 今振った人がトップから何マス後ろか（トップまたは同点なら0） |
| `leadMargin` | 今振った人が単独トップのとき、2番目との差。それ以外は null |
| `phaseBefore` / `phase` | 今回の前 / 後の局面。いちばん勝つ確率が高い人の確率で決める: 80%以上 `nearly_decided`（勝利が見えてきた）、65%以上 `pulling_away`（優勢）、それ以外 `close`（接戦）。確率の変化が3ポイント未満なら、境目をまたいでも局面は変わらないとする |
| `winChances` | 全員の勝つ確率。各項目は `player`・`word`（`very_likely` 85%以上、`likely` 60%以上、`even`、`unlikely` 15%超、`very_unlikely`）・`percent`・`percentBefore`。**判断の材料で、数字は言わせない** |

**`gameFlow` の項目**: `rollsSoFar`（これまでの出目の回数）・`leadChanges`（トップが入れ替わった回数。同点は数えない）・`leaderSinceTurn`（今のトップがトップになったターン。同点なら null）・`biggestJumpSoFar`（これまでで最大のハシゴ・ヘビの移動）。

`race` の位置は、最新の出目では実際の位置（位置の訂正を含む）を使う。`gameFlow` と `previousRoll` は出目の記録から計算するので、位置の訂正は反映されない。

### 勝つ確率の計算（[winning.py](../apps/server/src/commentator/services/llm/winning.py)）

プレイヤー同士は互いの移動に影響しないので、各プレイヤーが「あと何回振ればゴールできるか」は、そのマスだけで決まる独立した確率分布になる。
盤面ごとに「t 回振ってもまだゴールしていない確率」の表（最大400回分）を一度だけ作り、手番の順に組み合わせて、全員の「最初にゴールする確率」を正確に求める（乱数による試行は使わない）。
1回の計算は約0.2ミリ秒。乱数の試行（4万回）とは±1ポイント以内で一致することを確かめた。

先にゴールした人が勝ち（ゲームはそこで終わる）。ゴールを超える目ではその場にとどまるため、ゴール前にヘビの多い標準の盤では、85マスと19マスでも先の人の勝つ確率は約73%にとどまる。

### 例（架空のゲーム。標準の盤、2人、3ターン目の先攻がハシゴを登った）

出目は あおい 3、はると 5、あおい 2、はると 6、あおい 4（5→9、ハシゴで31）。一部だけを示す。

```json
{
 "reaction": "cheer",
 "biggestSwings": [
  {"kind": "hit_ladder", "player": "あおい", "roll": 4, "from": 9, "to": 31, "squares": 22, "impact": "small"},
  {"kind": "next_snake", "player": "はると", "roll": 5, "from": 16, "to": 6, "squares": 10, "impact": "small"},
  {"kind": "next_ladder", "player": "あおい", "roll": 5, "from": 36, "to": 44, "squares": 8, "impact": "small"}
 ],
 "race": {"leaderBefore": "はると", "leaderAfter": "あおい", "leadChanged": true, "overtook": ["はると"],
  "fellBehind": [], "behindLeader": 0, "leadMargin": 20, "phaseBefore": "close", "phase": "close",
  "winChances": [
   {"player": "あおい", "word": "even", "percent": 52, "percentBefore": 49},
   {"player": "はると", "word": "even", "percent": 48, "percentBefore": 51}
  ]},
 "previousRoll": {"player": "はると", "roll": 6, "from": 5, "final": 11, "jump": null, "reaction": "calm"}
}
```

序盤なので、22マスのハシゴでも勝つ確率は3ポイントしか変わらない（`impact` は small）。反応はハシゴの大きさ（22マス）から `cheer` になる。

## LLM の呼び出し

| | Claude（[providers.py](../apps/server/src/commentator/services/llm/providers.py)） | OpenAI |
|---|---|---|
| API | Messages API（`system` にシステムプロンプト、`messages` の user にユーザープロンプト） | Responses API（`instructions` と `input`） |
| 出力の上限 | `max_tokens` 4000 | `max_output_tokens` 4000 |
| その他 | Claude 5 世代などでは `effort: low`（短い実況なので考える量を減らし、速くする）。一部のモデルでは、断られたときにサーバー側で別のモデルに切り替える設定を付ける。`fastMode` がオンで Opus 5.5 なら `speed: fast`（高速モードの利用上限に当たったら、その回は普通の速さで送り直す。記録のモデル名は `claude-opus-5-5 (fast)`） | なし |
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

禁止語を含む普通の言葉（「始まったばかり」の「ばか」、「カスタネット」の「カス」など。`ALLOWED_WORDS`）は、照合の前に取り除く（2026-11-22。v4 が「始まったばかり」と言って代替文になったため）。

使えなかったとき、または LLM を呼べなかったときは、事実だけで作るテンプレート文に置き換える（`template_commentary`）。
例: 「あおいさん、出目は4！5マス目から9マス目へ進みます。ハシゴを登って一気に31マス目！現在トップを走っています。」
ゴールを超える目のときは「ゴールを超えてしまうので進めず、97マス目のままです。」となる。管理画面では「代替文」と理由が表示される。

## 記録と確認のしかた

- **管理画面「実況のプロンプト」**: 今の設定のシステムプロンプトと、最新の出目のユーザープロンプト（`GET /api/commentary/prompt`）。
- **管理画面「実況の記録」**: 実際に送ったプロンプト、応答の全文、最終的に使った文、所要時間、試行回数、エラー（最新20件）。
  SQLite の `commentary_requests` テーブルに保存される（`GET /api/commentary/requests`）。
- 通常のログ（`var/log/commentator.jsonl`）にはプロンプトや実況の文を書かない（[logging.md](operations/logging.md)）。

## 遊んだゲームで比べる（[compare_commentary.py](../apps/server/src/commentator/tools/compare_commentary.py)）

保存されたゲームを出目ごとに再現し、今のプロンプトと設定で実況を作り直して、ゲーム中に出た実況と並べた Markdown を書き出す。

```bash
.venv/bin/python -m commentator.tools.compare_commentary                 # いちばん最近実況したゲーム
.venv/bin/python -m commentator.tools.compare_commentary --turns 14-22   # 14〜22回目の出目だけ
.venv/bin/python -m commentator.tools.compare_commentary --game <ID>     # ゲームを指定
.venv/bin/python -m commentator.tools.compare_commentary --dry-run       # LLM を呼ばずにプロンプトだけ
```

- 出力は `var/prompt-comparisons/`（Git 管理外。プレイヤー名を含むため）。
- 各出目に、前の実況・新しい実況・反応の強さ・局面・勝つ確率の変化・上位の展開と、送ったユーザープロンプトを載せる。
- LLM を出目1回につき1回呼ぶ（1ゲーム26回なら26回）。サーバーと同じ検査・代替文の処理をするが、再試行はしない。
- 再現するのは出目だけで、手で行った位置の訂正・手番の変更は含まない。

## プロンプトを変えるとき

- システムプロンプトは `prompts/vN.py` の `system_prompt`、依頼文は `build_user_prompt`、JSON の中身は `CommentaryContext.facts()` と `build_context`。
- 起こり得たこと・次の可能性・勝負の状況は `outlook.py` の `analyze_turn`。しきい値は `IMPACT_LEVELS`（影響）、`reaction_level`（反応）、`phase_of`（局面）、`chance_word`（勝つ確率の言葉）、件数は `MAX_SWINGS`。
- 内容を変えるときは、今の版を書き換えずに新しい版を作る（上の「版の切り替え」）。記録に残るので、どの版で作った実況か後から分かる。
- 変えたら、遊んだゲームで比べる（上の比較の仕組み）。
- 長さの目安・上限は `LENGTH_LIMITS`、話し方は `STYLE_JA`・`STYLE_EN`（[prompts/common.py](../apps/server/src/commentator/services/llm/prompts/common.py)。全版で共通）。
- テストは `apps/server/tests/test_commentary.py` と `test_commentary_outlook.py`。
