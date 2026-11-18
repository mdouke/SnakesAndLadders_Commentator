import { useEffect, useState } from "react";

import {
  changeTurn,
  correctPosition,
  correctRoll,
  previewRollCorrection,
  restoreGame,
  resumeFromTurn
} from "./api";
import type { GameState, TurnRecord } from "./types";

type Run = (operation: () => Promise<GameState>) => void;

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
  abort: "中止", position_correction: "位置訂正", turn_change: "手番変更"
};

export function isEnded(game: GameState): boolean {
  return game.status === "completed" || game.status === "aborted";
}

/** Ask before changing the result of a finished game. */
export function confirmEnded(game: GameState): boolean {
  return !isEnded(game) || window.confirm(
    "このゲームは終了しています。結果を取り消して進行中に戻しますか？"
  );
}

export function playerName(game: GameState, playerId: string): string {
  return game.players.find((player) => player.id === playerId)?.displayName ?? "?";
}

function sourceText(turn: TurnRecord): string {
  if (turn.correctedFrom) return `訂正済み（元の出目 ${turn.correctedFrom.value}）`;
  return sourceLabels[turn.source];
}

function moveText(turn: TurnRecord): string {
  const path = [turn.before, turn.normalPosition];
  if (turn.finalPosition !== turn.normalPosition) path.push(turn.finalPosition);
  return path.join(" → ");
}

export function CorrectionsPanel({ game, run, onError }: PanelProps) {
  const [operator, setOperator] = useState("");
  const [reason, setReason] = useState("");
  const [positions, setPositions] = useState<Record<string, number>>({});
  const ended = isEnded(game);

  useEffect(() => {
    setPositions(Object.fromEntries(game.players.map((player) => [player.id, player.position])));
  }, [game]);

  if (!(["in_progress", "paused", "completed", "aborted"] as string[]).includes(game.status)) {
    return null;
  }
  const auditReady = () => {
    if (reason.trim() && operator.trim()) return true;
    onError("訂正理由と操作者名を入力してください");
    return false;
  };

  const correctTurn = async (turn: TurnRecord, value: number) => {
    if (!auditReady() || !confirmEnded(game)) return;
    try {
      const preview = await previewRollCorrection(
        game.id, turn.id, value, game.stateVersion, reason, operator, ended
      );
      const subject = `ターン${turn.number}（${playerName(game, turn.playerId)}）の出目を`
        + `${turn.roll}から${value}に訂正`;
      let message = `${subject}しますか？`;
      if (preview.requiresConfirmation) {
        const details = [
          ...preview.invalidatedTurns.map((item) =>
            `ターン${item.turnNumber}（${playerName(game, item.playerId)}・出目${item.value}）`),
          ...preview.invalidatedOperations.map((item) => operationNames[item.type] ?? item.type)
        ].join("、");
        message = `${subject}すると、${details}が無効になります。よろしいですか？`;
      }
      if (window.confirm(message)) {
        run(() => correctRoll(
          game.id, turn.id, value, game.stateVersion, reason, operator, ended,
          preview.requiresConfirmation
        ));
      }
    } catch (previewError) {
      onError(previewError instanceof Error ? previewError.message : "訂正内容を確認できませんでした");
    }
  };

  const canChangeTurn = game.status === "in_progress" || game.status === "paused";
  const turnCandidates = game.players.filter(
    (player) => !player.reachedGoal && player.id !== game.currentPlayerId
  );

  return (
    <section className="panel controls corrections">
      <h2>訂正</h2>
      <p className="muted">訂正はすべて操作者名と理由を記録します。Undoで取り消せます。</p>
      <div className="audit-fields">
        <input value={operator} onChange={(event) => setOperator(event.target.value)}
          placeholder="操作者名" aria-label="操作者名" />
        <input value={reason} onChange={(event) => setReason(event.target.value)}
          placeholder="訂正理由" aria-label="訂正理由" />
      </div>

      {game.turns.length > 0 && (
        <div className="correction-block">
          <h3>出目訂正</h3>
          {[...game.turns].reverse().map((turn) => (
            <details key={turn.id}>
              <summary>
                ターン {turn.number}・{playerName(game, turn.playerId)}：出目 {turn.roll}
                <span className="muted">（{moveText(turn)}）</span>
              </summary>
              <div className="dice-row compact">
                {DICE.filter((value) => value !== turn.roll).map((value) => (
                  <button key={value} onClick={() => correctTurn(turn, value)}>{value}</button>
                ))}
              </div>
            </details>
          ))}
        </div>
      )}

      <div className="correction-block">
        <h3>位置訂正</h3>
        <div className="correction-list">
          {game.players.map((player) => (
            <label key={player.id}>
              {player.displayName}
              <input type="number" min="0" max="99" value={positions[player.id] ?? player.position}
                onChange={(event) => setPositions((current) => ({
                  ...current, [player.id]: Number(event.target.value)
                }))} />
              <button onClick={() => {
                const position = positions[player.id] ?? player.position;
                if (auditReady()
                  && window.confirm(`${player.displayName}の位置を${player.position}から${position}に訂正しますか？`)
                  && confirmEnded(game)) {
                  run(() => correctPosition(
                    game.id, player.id, position, game.stateVersion, reason, operator, ended
                  ));
                }
              }}>訂正</button>
            </label>
          ))}
        </div>
      </div>

      {canChangeTurn && turnCandidates.length > 0 && (
        <div className="correction-block">
          <h3>手番変更</h3>
          <p className="muted">
            現在は{game.currentPlayerId ? playerName(game, game.currentPlayerId) : "-"}の手番です。
          </p>
          <div className="button-row">
            {turnCandidates.map((player) => (
              <button key={player.id} className="secondary" onClick={() => {
                if (auditReady() && window.confirm(`${player.displayName}の手番に変更しますか？`)) {
                  run(() => changeTurn(game.id, player.id, game.stateVersion, reason, operator));
                }
              }}>{player.displayName}の手番にする</button>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

export function HistoryPanel({ game, run }: PanelProps) {
  const [restoreVersion, setRestoreVersion] = useState(0);
  const ended = isEnded(game);
  if (game.turns.length === 0) return null;
  const latest = game.turns[game.turns.length - 1];

  const resume = (turn: TurnRecord | null) => {
    const label = turn
      ? `ターン${turn.number}（${playerName(game, turn.playerId)}・出目${turn.roll}）の後`
      : "最初の出目の前";
    const dropped = turn ? game.turns.length - turn.number : game.turns.length;
    if (window.confirm(`${label}から再開します。後の${dropped}ターンは無効になります（Undoで戻せます）。`)
      && confirmEnded(game)) {
      run(() => resumeFromTurn(game.id, turn ? turn.id : null, game.stateVersion, ended));
    }
  };

  return (
    <section className="panel controls history">
      <h2>履歴と任意ターンからの再開</h2>
      <div className="table-scroll">
        <table className="turn-table">
          <thead>
            <tr><th>ターン</th><th>プレイヤー</th><th>出目</th><th>移動</th><th>入力</th><th /></tr>
          </thead>
          <tbody>
            {[...game.turns].reverse().map((turn) => (
              <tr key={turn.id}>
                <td>{turn.number}</td>
                <td>{playerName(game, turn.playerId)}</td>
                <td>{turn.roll}</td>
                <td>
                  {moveText(turn)}
                  {turn.jumpType === "ladder" && " ハシゴ"}
                  {turn.jumpType === "snake" && " ヘビ"}
                  {turn.overshoot && " 超過"}
                </td>
                <td>{sourceText(turn)}</td>
                <td>
                  {turn.id !== latest.id && (
                    <button className="secondary small" onClick={() => resume(turn)}>この後から再開</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="button-row">
        <button className="secondary" onClick={() => resume(null)}>最初の出目の前から再開</button>
      </div>
      <details className="advanced">
        <summary>状態バージョンで復元（上級者向け）</summary>
        <p className="muted">
          保存済みのスナップショットへ戻します。その後に行った訂正も取り消されます。
          通常は上の「この後から再開」を使ってください。
        </p>
        <div className="audit-fields">
          <input type="number" min="0" max={game.stateVersion} value={restoreVersion}
            aria-label="状態バージョン"
            onChange={(event) => setRestoreVersion(Number(event.target.value))} />
          <button className="danger" onClick={() => {
            if (window.confirm(`状態バージョン${restoreVersion}へ復元しますか？`) && confirmEnded(game)) {
              run(() => restoreGame(game.id, restoreVersion, game.stateVersion, ended));
            }
          }}>復元</button>
        </div>
      </details>
    </section>
  );
}
