import { type ReactNode, useEffect, useState } from "react";

import {
  changeTurn,
  correctPosition,
  correctRoll,
  previewRollCorrection,
  restoreGame,
  resumeFromTurn
} from "./api";
import type { GameState, TimelineEntry, TurnRecord } from "./types";

type Run = (operation: () => Promise<GameState>, done?: string) => void;

interface PanelProps {
  game: GameState;
  run: Run;
  onError: (message: string | null) => void;
}

const DICE = [1, 2, 3, 4, 5, 6];

const sourceLabels: Record<TurnRecord["source"], string> = {
  manual: "手動",
  automatic: "自動認識",
  reviewed: "確認して確定",
  corrected: "修正して確定"
};

const operationNames: Record<string, string> = {
  start: "ゲーム開始", pause: "一時停止", resume: "再開", suspend: "保存終了",
  abort: "中止", reopen: "中止の取り消し", position_correction: "位置訂正", turn_change: "手番変更"
};

export function isEnded(game: GameState): boolean {
  return game.status === "completed" || game.status === "aborted";
}

/** Ask before changing the result of a finished game. */
export function confirmEnded(game: GameState): boolean {
  return game.status !== "completed" || window.confirm(
    "このゲームは終了しています。結果を取り消して進行中に戻しますか？"
  );
}

export function playerName(game: GameState, playerId: string | null): string {
  return game.players.find((player) => player.id === playerId)?.displayName ?? "?";
}

function roundOf(game: GameState, turnId: string | null): number | string {
  return game.turns.find((turn) => turn.id === turnId)?.round ?? "?";
}

function moveText(turn: TurnRecord): string {
  const path = [turn.before, turn.normalPosition];
  if (turn.finalPosition !== turn.normalPosition) path.push(turn.finalPosition);
  return path.join(" → ");
}

function whoWhy(operator: string | null, reason: string | null): string {
  const parts = [operator, reason].filter((item): item is string => Boolean(item));
  return parts.length > 0 ? `（${parts.join("・")}）` : "";
}

const pauseReasons: Record<string, string> = {
  after_correction: "一時停止（訂正のため自動）",
  process_interrupted: "一時停止（異常終了を検出）"
};

function operationText(game: GameState, entry: TimelineEntry): string {
  const name = operationNames[entry.type] ?? entry.type;
  if (entry.type === "pause") return pauseReasons[entry.reason ?? ""] ?? name;
  if (entry.type === "position_correction") {
    return `${name}：${playerName(game, entry.playerId)} ${entry.beforePosition ?? "?"}→${entry.position ?? "?"}`;
  }
  if (entry.type === "turn_change") {
    return `${name}：${playerName(game, entry.previousPlayerId)}→${playerName(game, entry.playerId)}`;
  }
  return name;
}

interface Group {
  round: number;
  entries: { entry: TimelineEntry; turn: TurnRecord | null }[];
}

/** Group the history by display turn; operations join the turn they happened in. */
function groupTimeline(game: GameState): Group[] {
  const turns = new Map(game.turns.map((turn) => [turn.id, turn]));
  const groups: Group[] = [];
  let current: Group = { round: 0, entries: [] };
  for (const entry of game.timeline) {
    const turn = entry.type === "roll" && entry.turnId ? turns.get(entry.turnId) ?? null : null;
    if (turn && turn.round !== current.round) {
      if (current.entries.length > 0) groups.push(current);
      current = { round: turn.round, entries: [] };
    }
    current.entries.push({ entry, turn });
  }
  if (current.entries.length > 0) groups.push(current);
  return groups.reverse().map((group) => ({ ...group, entries: [...group.entries].reverse() }));
}

const HISTORY_OPEN_KEY = "commentator.historyPanelOpen";

function readHistoryOpen(): boolean {
  try {
    return window.localStorage.getItem(HISTORY_OPEN_KEY) !== "false";
  } catch {
    return true;
  }
}

function saveHistoryOpen(open: boolean): void {
  try {
    window.localStorage.setItem(HISTORY_OPEN_KEY, String(open));
  } catch {
    // Remembering the toggle is a convenience only.
  }
}

/** Shown next to the title so the folded panel still tells where the game is. */
function historySummary(game: GameState): string {
  const last = game.turns[game.turns.length - 1];
  if (!last) return "まだ出目はありません";
  return `${game.turns.length}回・最新 ${playerName(game, last.playerId)} 出目${last.roll}`;
}

export function HistoryPanel({ game, run, onError }: PanelProps) {
  const [operator, setOperator] = useState("");
  const [reason, setReason] = useState("");
  const [openTurn, setOpenTurn] = useState<string | null>(null);
  const [panelOpen, setPanelOpen] = useState(readHistoryOpen);
  const [tool, setTool] = useState<"position" | "turn" | null>(null);
  const [positionPlayer, setPositionPlayer] = useState(game.players[0]?.id ?? "");
  const [position, setPosition] = useState(0);
  const [restoreVersion, setRestoreVersion] = useState(0);
  const ended = isEnded(game);
  const aborted = game.status === "aborted";
  const started = game.status !== "ready";

  useEffect(() => {
    const player = game.players.find((item) => item.id === positionPlayer);
    setPosition(player?.position ?? 0);
  }, [game, positionPlayer]);

  if (!started) return null;

  const auditReady = () => {
    if (reason.trim() && operator.trim()) return true;
    onError("操作者名と理由を入力してください");
    return false;
  };
  const lastTurn = game.turns.length > 0 ? game.turns[game.turns.length - 1] : null;
  const lastEntry = game.timeline[game.timeline.length - 1];

  const correctTurn = async (turn: TurnRecord, value: number) => {
    if (!auditReady() || !confirmEnded(game)) return;
    try {
      const preview = await previewRollCorrection(
        game.id, turn.id, value, game.stateVersion, reason, operator, ended
      );
      const subject = `ターン${turn.round}（${playerName(game, turn.playerId)}）の出目を`
        + `${turn.roll}から${value}に直`;
      let message = `${subject}します。後のターンは新しい出目で計算し直します。よろしいですか？`;
      if (preview.requiresConfirmation) {
        const details = [
          ...preview.invalidatedTurns.map((item) =>
            `ターン${roundOf(game, item.turnId)}（${playerName(game, item.playerId)}・出目${item.value}）`),
          ...preview.invalidatedOperations.map((item) => operationNames[item.type] ?? item.type)
        ].join("、");
        message = `${subject}すと、${details}が無効になります。よろしいですか？`;
      }
      if (window.confirm(message)) {
        run(() => correctRoll(
          game.id, turn.id, value, game.stateVersion, reason, operator, ended,
          preview.requiresConfirmation
        ), `ターン${turn.round}（${playerName(game, turn.playerId)}）の出目を${value}に直しました`);
      }
    } catch (previewError) {
      onError(previewError instanceof Error ? previewError.message : "訂正内容を確認できませんでした");
    }
  };

  const rewind = (turn: TurnRecord | null) => {
    if (!auditReady()) return;
    const label = turn
      ? `ターン${turn.round}（${playerName(game, turn.playerId)}・出目${turn.roll}）の後`
      : "最初の出目の前";
    const dropped = turn ? game.turns.length - turn.number : game.turns.length;
    if (window.confirm(`${label}まで戻します。その後の${dropped}回の出目と操作は無効になります（Undoで戻せます）。`)
      && confirmEnded(game)) {
      run(() => resumeFromTurn(
        game.id, turn ? turn.id : null, game.stateVersion, reason, operator, ended
      ), `${label}まで戻しました（Undoで戻せます）`);
    }
  };

  const canChangeTurn = game.status === "in_progress" || game.status === "paused";
  const turnCandidates = game.players.filter(
    (player) => !player.reachedGoal && player.id !== game.currentPlayerId
  );
  const positionPlayerName = playerName(game, positionPlayer);
  let toolPanel: ReactNode = null;
  if (tool === "position") {
    toolPanel = (
      <div className="tool-panel">
        <select value={positionPlayer} aria-label="プレイヤー"
          onChange={(event) => setPositionPlayer(event.target.value)}>
          {game.players.map((player) => (
            <option key={player.id} value={player.id}>{player.displayName}（今 {player.position}）</option>
          ))}
        </select>
        <input type="number" min={0} max={99} value={position} aria-label="新しい位置"
          onChange={(event) => setPosition(Number(event.target.value))} />
        <button onClick={() => {
          if (auditReady()
            && window.confirm(`${positionPlayerName}の位置を${position}に訂正しますか？`)
            && confirmEnded(game)) {
            run(() => correctPosition(
              game.id, positionPlayer, position, game.stateVersion, reason, operator, ended
            ), `${positionPlayerName}の位置を${position}に訂正しました`);
          }
        }}>訂正</button>
      </div>
    );
  } else if (tool === "turn") {
    toolPanel = (
      <div className="tool-panel">
        <span className="muted">今は{playerName(game, game.currentPlayerId)}の手番です。</span>
        {turnCandidates.map((player) => (
          <button key={player.id} className="secondary" onClick={() => {
            if (auditReady() && window.confirm(`${player.displayName}の手番に変更しますか？`)) {
              run(() => changeTurn(game.id, player.id, game.stateVersion, reason, operator),
                `${player.displayName}の手番に変更しました`);
            }
          }}>{player.displayName}の手番にする</button>
        ))}
      </div>
    );
  }

  return (
    <details className="panel controls history" open={panelOpen}
      onToggle={(event) => {
        const open = (event.target as HTMLDetailsElement).open;
        setPanelOpen(open);
        saveHistoryOpen(open);
      }}>
      <summary>履歴と訂正<span className="muted">{historySummary(game)}</span></summary>
      {aborted ? (
        <p className="muted">中止したゲームは訂正できません。続ける場合は「中止を取り消して再開」を押してください。</p>
      ) : (
        <>
          <div className="audit-fields">
            <input value={operator} onChange={(event) => setOperator(event.target.value)}
              placeholder="操作者名" aria-label="操作者名" />
            <input value={reason} onChange={(event) => setReason(event.target.value)}
              placeholder="理由" aria-label="理由" />
            <span className="muted">
              訂正と「ここまで戻す」には両方の入力が必要です。すべて記録され、Undoで取り消せます。
              訂正するとゲームは一時停止し、「再開」を押すまで進みません。
            </span>
          </div>
          <div className="current-tools">
            <strong>今の状態を直す</strong>
            <button className={tool === "position" ? "" : "secondary"}
              onClick={() => setTool(tool === "position" ? null : "position")}>位置を訂正</button>
            {canChangeTurn && turnCandidates.length > 0 && (
              <button className={tool === "turn" ? "" : "secondary"}
                onClick={() => setTool(tool === "turn" ? null : "turn")}>手番を変更</button>
            )}
          </div>
          {toolPanel}
        </>
      )}

      <div className="timeline">
        {groupTimeline(game).map((group) => (
          <section key={group.round} className="timeline-group">
            <h3>{group.round === 0 ? "開始" : `ターン${group.round}`}</h3>
            <ul>
              {group.entries.map(({ entry, turn }) => {
                if (!turn) {
                  return (
                    <li key={entry.id} className="timeline-op">
                      <span>{operationText(game, entry)}</span>
                      {entry.type !== "pause" && (
                        <span className="muted">{whoWhy(entry.operator, entry.reason)}</span>
                      )}
                    </li>
                  );
                }
                const open = openTurn === turn.id;
                const canRewind = !aborted && (turn.id !== lastTurn?.id || lastEntry?.turnId !== turn.id);
                return (
                  <li key={entry.id} className={`timeline-roll${open ? " open" : ""}`}>
                    <button className="timeline-row" aria-expanded={open}
                      onClick={() => setOpenTurn(open ? null : turn.id)}>
                      <span className="token small-token"
                        style={{ backgroundColor: game.players.find((player) => player.id === turn.playerId)?.color }} />
                      <span className="timeline-player">{playerName(game, turn.playerId)}</span>
                      <span>出目 <b>{turn.roll}</b></span>
                      <span>
                        {moveText(turn)}
                        {turn.jumpType === "ladder" && " ハシゴ"}
                        {turn.jumpType === "snake" && " ヘビ"}
                        {turn.overshoot && " 超過"}
                      </span>
                      <span className="muted">
                        {turn.correctedFrom ? `訂正済み（元の出目 ${turn.correctedFrom.value}）` : sourceLabels[turn.source]}
                      </span>
                      <span className="timeline-toggle">{open ? "閉じる" : "操作"}</span>
                    </button>
                    {open && (
                      <div className="timeline-actions">
                        {turn.correctedFrom && (
                          <p className="muted">
                            元の出目 {turn.correctedFrom.value}（{sourceLabels[turn.correctedFrom.source]}）を訂正
                            {whoWhy(turn.correctedFrom.operator, turn.correctedFrom.reason)}
                          </p>
                        )}
                        {turn.confidence && (
                          <p className="muted">認識の信頼度 {turn.confidence.total.toFixed(2)}</p>
                        )}
                        {!aborted && (
                          <>
                            <div>
                              <strong>出目を直す</strong>
                              <span className="muted">（後のターンは新しい出目で計算し直す）</span>
                              <div className="dice-row compact">
                                {DICE.filter((value) => value !== turn.roll).map((value) => (
                                  <button key={value} onClick={() => correctTurn(turn, value)}>{value}</button>
                                ))}
                              </div>
                            </div>
                            {canRewind && (
                              <div>
                                <button className="secondary" onClick={() => rewind(turn)}>ここまで戻す</button>
                                <span className="muted">（この後の出目と操作は無効になる）</span>
                              </div>
                            )}
                          </>
                        )}
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          </section>
        ))}
      </div>

      {!aborted && game.turns.length > 0 && (
        <div className="button-row">
          <button className="secondary" onClick={() => rewind(null)}>最初の出目の前まで戻す</button>
        </div>
      )}
      {game.auditLog.length > 0 && (
        <details className="advanced">
          <summary>変更の記録（{game.auditLog.length}件）</summary>
          <ul className="audit-log">
            {game.auditLog.map((entry) => (
              <li key={entry.id}>
                <time>{new Date(entry.occurredAt).toLocaleString([], { dateStyle: "short", timeStyle: "short" })}</time>
                <span>{entry.label}</span>
                <span className="muted">{whoWhy(entry.operator, entry.reason)}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
      {!aborted && (
        <details className="advanced">
          <summary>状態バージョンで復元（上級者向け）</summary>
          <p className="muted">
            保存済みのスナップショットへ戻します。その後に行った訂正も取り消されます。
            通常は各ターンの「ここまで戻す」を使ってください。
          </p>
          <div className="audit-fields">
            <input type="number" min="0" max={game.stateVersion} value={restoreVersion}
              aria-label="状態バージョン"
              onChange={(event) => setRestoreVersion(Number(event.target.value))} />
            <button className="danger" onClick={() => {
              if (window.confirm(`状態バージョン${restoreVersion}へ復元しますか？`) && confirmEnded(game)) {
                run(() => restoreGame(game.id, restoreVersion, game.stateVersion, ended),
                  `状態バージョン${restoreVersion}へ復元しました`);
              }
            }}>復元</button>
          </div>
        </details>
      )}
    </details>
  );
}
