import { useCallback, useEffect, useMemo, useState } from "react";

import {
  type NewPlayer,
  createGame,
  deleteBoard,
  getBoard,
  listBoards,
  saveBoard,
  validateBoard
} from "./api";
import { BoardSvg } from "./Board";
import type { Board, BoardPresetSummary, GameState } from "./types";

const MAX_PLAYERS = 6;
const MIN_PLAYERS = 2;

// Fixed order, validated for color-vision deficiency on adjacent pairs (dataviz
// reference palette). New players take the next unused color in this order.
const COLORS: { value: string; label: string }[] = [
  { value: "#2a78d6", label: "青" },
  { value: "#eb6834", label: "オレンジ" },
  { value: "#1baf7a", label: "水色" },
  { value: "#eda100", label: "黄" },
  { value: "#e87ba4", label: "ピンク" },
  { value: "#008300", label: "緑" },
  { value: "#6250d6", label: "紫" },
  { value: "#e34948", label: "赤" }
];

interface PlayerDraft {
  key: number;
  name: string;
  color: string;
  spokenName: string;
}

let nextKey = 1;

function draft(used: string[]): PlayerDraft {
  const color = COLORS.find((item) => !used.includes(item.value))?.value ?? COLORS[0].value;
  return { key: nextKey++, name: "", color, spokenName: "" };
}

function defaultPlayers(): PlayerDraft[] {
  const first = draft([]);
  return [first, draft([first.color])];
}

/** Player numbers always follow the turn order; only the system assigns them. */
function playerNumber(index: number): string {
  return `プレイヤー${index + 1}`;
}

export function GameSetupPanel({
  onCreate, onCancel
}: {
  onCreate: (operation: () => Promise<GameState>) => void;
  onCancel?: () => void;
}) {
  const [players, setPlayers] = useState<PlayerDraft[]>(defaultPlayers);
  const [presets, setPresets] = useState<BoardPresetSummary[]>([]);
  const [boardId, setBoardId] = useState("standard");
  const [preview, setPreview] = useState<Board | null>(null);
  const [editing, setEditing] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const reloadPresets = useCallback(() => {
    listBoards().then(({ presets: found }) => setPresets(found)).catch(() => undefined);
  }, []);
  useEffect(reloadPresets, [reloadPresets]);
  useEffect(() => {
    getBoard(boardId).then(setPreview).catch(() => setPreview(null));
  }, [boardId]);

  const update = (key: number, change: Partial<PlayerDraft>) =>
    setPlayers((current) => current.map((item) => (item.key === key ? { ...item, ...change } : item)));
  const move = (index: number, offset: number) => setPlayers((current) => {
    const next = [...current];
    const [item] = next.splice(index, 1);
    next.splice(index + offset, 0, item);
    return next;
  });

  const problems = useMemo(() => {
    const result: string[] = [];
    const colors = players.map((item) => item.color.toLowerCase());
    if (new Set(colors).size !== colors.length) result.push("同じ色のプレイヤーがいます");
    return result;
  }, [players]);

  const submit = () => {
    if (problems.length > 0) {
      setMessage(problems.join("。"));
      return;
    }
    const payload: NewPlayer[] = players.map((item, index) => ({
      // A blank name falls back to the player's number in turn order.
      name: item.name.trim() || playerNumber(index),
      color: item.color,
      ...(item.spokenName.trim() ? { spokenName: item.spokenName.trim() } : {})
    }));
    onCreate(() => createGame(payload, boardId));
  };

  return (
    <section className="panel setup">
      <h2>新しいゲームを準備</h2>
      <h3>プレイヤー（{players.length}人・最大{MAX_PLAYERS}人）</h3>
      <p className="muted">
        上から順に手番が回ります。プレイヤー番号は並び順で自動的に付きます。
        名前を空欄にすると番号がそのまま名前になります。読み方は実況の読み上げに使います。
      </p>
      <ol className="setup-players">
        {players.map((player, index) => (
          <li key={player.key}>
            <span className="setup-number">{playerNumber(index)}</span>
            <span className="token" style={{ backgroundColor: player.color }} />
            <input value={player.name} aria-label={`${playerNumber(index)}の名前`} maxLength={80}
              placeholder={`名前（空欄なら${playerNumber(index)}）`}
              onChange={(event) => update(player.key, { name: event.target.value })} />
            <input value={player.spokenName} placeholder="読み方（任意）" maxLength={120}
              aria-label={`${playerNumber(index)}の読み方`}
              onChange={(event) => update(player.key, { spokenName: event.target.value })} />
            <select value={player.color} aria-label={`${playerNumber(index)}の色`}
              onChange={(event) => update(player.key, { color: event.target.value })}>
              {COLORS.map((color) => (
                <option key={color.value} value={color.value}
                  disabled={players.some((other) => other.key !== player.key && other.color === color.value)}>
                  {color.label}
                </option>
              ))}
            </select>
            <span className="setup-order">
              <button className="secondary small" disabled={index === 0}
                aria-label="上へ" onClick={() => move(index, -1)}>↑</button>
              <button className="secondary small" disabled={index === players.length - 1}
                aria-label="下へ" onClick={() => move(index, 1)}>↓</button>
              <button className="danger small" disabled={players.length <= MIN_PLAYERS}
                onClick={() => setPlayers((current) => current.filter((item) => item.key !== player.key))}>
                削除
              </button>
            </span>
          </li>
        ))}
      </ol>
      <button className="secondary" disabled={players.length >= MAX_PLAYERS}
        onClick={() => setPlayers((current) => [...current, draft(current.map((item) => item.color))])}>
        プレイヤーを追加
      </button>

      <h3>盤面</h3>
      <div className="setup-board">
        <div>
          <select value={boardId} aria-label="盤面" onChange={(event) => setBoardId(event.target.value)}>
            {presets.map((preset) => (
              <option key={preset.id} value={preset.id}>
                {preset.name}（ヘビ{preset.snakeCount}・ハシゴ{preset.ladderCount}）{preset.builtIn ? "・標準" : ""}
              </option>
            ))}
          </select>
          <button className="secondary" onClick={() => setEditing((value) => !value)}>
            {editing ? "盤面の編集を閉じる" : "盤面を作成・編集"}
          </button>
        </div>
        {!editing && preview && <BoardSvg board={preview} />}
      </div>
      {editing && (
        <BoardPresetEditor
          key={preview?.id ?? "new"}
          base={preview}
          builtIn={presets.find((preset) => preset.id === boardId)?.builtIn ?? true}
          onSaved={(saved) => {
            reloadPresets();
            setBoardId(saved.id);
            setPreview(saved);
            setEditing(false);
          }}
          onDeleted={() => {
            reloadPresets();
            setBoardId("standard");
            setEditing(false);
          }}
        />
      )}

      {message && <p className="error">{message}</p>}
      <div className="button-row setup-actions">
        {onCancel && <button className="secondary" onClick={onCancel}>キャンセル</button>}
        <button onClick={submit}>この内容でゲームを作成</button>
      </div>
    </section>
  );
}

interface JumpDraft {
  key: number;
  type: "ladder" | "snake";
  from: number;
  to: number;
}

function BoardPresetEditor({
  base, builtIn, onSaved, onDeleted
}: {
  base: Board | null;
  builtIn: boolean;
  onSaved: (board: Board) => void;
  onDeleted: () => void;
}) {
  // Built-in boards are read-only, so editing one starts a copy with a new ID.
  const [id, setId] = useState(base && !builtIn ? base.id : `${base?.id ?? "board"}-copy`);
  const [name, setName] = useState(base && !builtIn ? base.name : `${base?.name ?? "新しい盤面"}（コピー）`);
  const [jumps, setJumps] = useState<JumpDraft[]>(
    () => (base?.jumps ?? []).map((jump) => ({ key: nextKey++, ...jump }))
  );
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);

  const board: Board = {
    id: id.trim(), name: name.trim(), goal: 100,
    jumps: jumps.map(({ type, from, to }) => ({ type, from, to }))
  };
  const updateJump = (key: number, change: Partial<JumpDraft>) => {
    setResult(null);
    setJumps((current) => current.map((item) => (item.key === key ? { ...item, ...change } : item)));
  };

  const check = async (): Promise<boolean> => {
    try {
      const validation = await validateBoard(board);
      setResult(validation.valid
        ? { ok: true, text: "問題ありません" }
        : { ok: false, text: validation.message ?? "盤面に問題があります" });
      return validation.valid;
    } catch (error) {
      setResult({ ok: false, text: error instanceof Error ? error.message : "検証できませんでした" });
      return false;
    }
  };

  return (
    <div className="board-editor">
      <div className="audit-fields">
        <label>名前 <input value={name} maxLength={80} onChange={(event) => setName(event.target.value)} /></label>
        <label>ID（英小文字・数字・ハイフン）
          <input value={id} maxLength={40} onChange={(event) => setId(event.target.value.toLowerCase())} />
        </label>
      </div>
      <p className="muted">
        ヘビは下へ、ハシゴは上へ移動します。移動先を別のヘビ・ハシゴの開始マスにはできません。
      </p>
      <div className="board-editor-body">
        <div className="jump-list">
          {jumps.map((jump) => (
            <div className="jump-row" key={jump.key}>
              <select value={jump.type} aria-label="種類"
                onChange={(event) => updateJump(jump.key, { type: event.target.value as JumpDraft["type"] })}>
                <option value="ladder">ハシゴ</option>
                <option value="snake">ヘビ</option>
              </select>
              <input type="number" min={1} max={99} value={jump.from} aria-label="開始マス"
                onChange={(event) => updateJump(jump.key, { from: Number(event.target.value) })} />
              <span>→</span>
              <input type="number" min={1} max={100} value={jump.to} aria-label="移動先"
                onChange={(event) => updateJump(jump.key, { to: Number(event.target.value) })} />
              <button className="danger small"
                onClick={() => setJumps((current) => current.filter((item) => item.key !== jump.key))}>削除</button>
            </div>
          ))}
          <div className="button-row">
            <button className="secondary small" onClick={() => setJumps((current) => [
              ...current, { key: nextKey++, type: "ladder", from: 2, to: 20 }
            ])}>ハシゴを追加</button>
            <button className="secondary small" onClick={() => setJumps((current) => [
              ...current, { key: nextKey++, type: "snake", from: 30, to: 10 }
            ])}>ヘビを追加</button>
          </div>
        </div>
        <BoardSvg board={board} />
      </div>
      {result && <p className={result.ok ? "notice" : "error"}>{result.text}</p>}
      <div className="button-row">
        <button className="secondary" onClick={check}>検証</button>
        <button onClick={async () => {
          if (!(await check())) return;
          try {
            onSaved(await saveBoard(board));
          } catch (error) {
            setResult({ ok: false, text: error instanceof Error ? error.message : "保存できませんでした" });
          }
        }}>保存</button>
        {base && !builtIn && (
          <button className="danger" onClick={async () => {
            if (!window.confirm(`盤面「${base.name}」を削除しますか？（作成済みのゲームには影響しません）`)) return;
            try {
              await deleteBoard(base.id);
              onDeleted();
            } catch (error) {
              setResult({ ok: false, text: error instanceof Error ? error.message : "削除できませんでした" });
            }
          }}>この盤面を削除</button>
        )}
      </div>
    </div>
  );
}
