import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiRequestError,
  activateGame,
  correctPosition,
  correctRoll,
  createDefaultGame,
  gameAction,
  getCurrentGame,
  getGames,
  previewRollCorrection,
  restoreGame,
  submitRoll
} from "./api";
import type { GameState } from "./types";

const statusLabels: Record<GameState["status"], string> = {
  ready: "開始待ち",
  in_progress: "進行中",
  paused: "一時停止中",
  suspended: "保存終了",
  completed: "順位確定・ゲーム終了",
  aborted: "ゲーム中止"
};

function PlayerList({ game }: { game: GameState }) {
  return (
    <div className="players">
      {game.players.map((player) => (
        <article className={`player-card ${player.isCurrent ? "current" : ""}`} key={player.id}>
          <span className="token" style={{ backgroundColor: player.color }} />
          <div>
            <h3>{player.displayName}</h3>
            <p>{player.position}マス目 ・ {player.rank}位{player.rankConfirmed ? "確定" : ""}</p>
            {player.reachedGoal && <strong>ゴール</strong>}
          </div>
        </article>
      ))}
    </div>
  );
}

function AdminView() {
  const [game, setGame] = useState<GameState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [savedGames, setSavedGames] = useState<GameState[]>([]);
  const [busy, setBusy] = useState(false);
  const [positions, setPositions] = useState<Record<string, number>>({});
  const [reason, setReason] = useState("");
  const [operator, setOperator] = useState("");
  const [restoreVersion, setRestoreVersion] = useState(0);
  const pending = useRef(false);

  const run = useCallback(async (operation: () => Promise<GameState>) => {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    try {
      setError(null);
      setGame(await operation());
      getGames().then(setSavedGames).catch(() => undefined);
    } catch (reason) {
      if (reason instanceof ApiRequestError && reason.code === "state_version_conflict") {
        setError("別の操作でゲームが更新されました。最新状態を読み込みました。");
      } else {
        setError(reason instanceof Error ? reason.message : "操作に失敗しました");
      }
      await getCurrentGame().then(setGame).catch(() => undefined);
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    getCurrentGame().then(setGame).catch(() => undefined);
    getGames().then(setSavedGames).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (game) {
      setPositions(Object.fromEntries(game.players.map((player) => [player.id, player.position])));
    }
  }, [game]);

  const isEnded = game?.status === "completed" || game?.status === "aborted";
  const confirmEnded = () => !isEnded || window.confirm(
    "このゲームは終了しています。結果を取り消して進行中に戻しますか？"
  );
  const auditReady = () => {
    if (reason.trim() && operator.trim()) return true;
    setError("訂正理由と操作者名を入力してください");
    return false;
  };
  const prepareRollCorrection = async (turnId: string, turnNumber: number, value: number) => {
    if (!game || !auditReady() || !confirmEnded()) return;
    try {
      const preview = await previewRollCorrection(
        game.id, turnId, value, game.stateVersion,
        reason, operator, Boolean(isEnded)
      );
      let confirmed = true;
      if (preview.requiresConfirmation) {
        const turnDetails = preview.invalidatedTurns.map((item) =>
          `ターン${item.turnNumber}（出目${item.value}）`
        );
        const operationNames: Record<string, string> = {
          start: "ゲーム開始", pause: "一時停止", resume: "再開",
          suspend: "保存終了", abort: "中止", position_correction: "位置訂正"
        };
        const operationDetails = preview.invalidatedOperations.map((item) =>
          operationNames[item.type] ?? item.type
        );
        const details = [...turnDetails, ...operationDetails].join("、");
        confirmed = window.confirm(`${details}を無効にします。よろしいですか？`);
      } else {
        confirmed = window.confirm(`ターン${turnNumber}の出目を${value}に訂正しますか？`);
      }
      if (confirmed) {
        run(() => correctRoll(
          game.id, turnId, value, game.stateVersion,
          reason, operator, Boolean(isEnded), preview.requiresConfirmation
        ));
      }
    } catch (previewError) {
      setError(previewError instanceof Error ? previewError.message : "訂正内容を確認できませんでした");
    }
  };

  return (
    <main>
      <header>
        <div>
          <p className="eyebrow">管理者画面</p>
          <h1>Snakes & Ladders</h1>
        </div>
        <a href="/player" target="_blank" rel="noreferrer">プレイヤー画面を開く</a>
      </header>

      <fieldset disabled={busy} className="operation-area">
      {savedGames.some((item) => ["suspended", "paused"].includes(item.status)) && (
        <section className="panel saved-games">
          <h2>再開できるゲーム</h2>
          <div className="saved-game-list">
            {savedGames.filter((item) => ["suspended", "paused"].includes(item.status)).map((item) => (
              <button className="secondary" key={item.id} onClick={() => run(() => activateGame(item))}>
                {statusLabels[item.status]}・ターン {item.turnNumber}・{item.players.map((player) => player.displayName).join(" / ")}
              </button>
            ))}
          </div>
        </section>
      )}
      {!game ? (
        <section className="panel empty">
          <h2>ゲームを準備</h2>
          <p>まずはデフォルトの2人でゲームを作成します。</p>
          <button onClick={() => run(createDefaultGame)}>新しいゲーム</button>
        </section>
      ) : (
        <>
          <section className="status-bar">
            <span>{statusLabels[game.status]}</span>
            <span>ターン {game.turnNumber}</span>
          </section>
          {game.interruption && (
            <section className="interruption-banner">
              <strong>前回は異常終了しました。</strong>
              <span>ターン{game.turnNumber}・実物の盤面と画面の位置を確認してから再開してください。</span>
            </section>
          )}
          <PlayerList game={game} />
          <section className="panel controls">
            <h2>ゲーム操作</h2>
            <div className="button-row">
              {(game.status === "completed" || game.status === "aborted") && (
                <button onClick={() => run(createDefaultGame)}>新しいゲーム</button>
              )}
              {game.status === "ready" && (
                <button onClick={() => run(() => gameAction(game.id, "start", game.stateVersion))}>開始</button>
              )}
              {game.status === "in_progress" && (
                <>
                  <button className="secondary" onClick={() => run(() => gameAction(game.id, "pause", game.stateVersion))}>一時停止</button>
                  <button className="secondary" onClick={() => run(() => gameAction(game.id, "suspend", game.stateVersion))}>保存して終了</button>
                </>
              )}
              {(game.status === "paused" || game.status === "suspended") && (
                <button onClick={() => run(() => gameAction(game.id, "resume", game.stateVersion))}>再開</button>
              )}
              {!(["completed", "aborted"] as string[]).includes(game.status) && (
                <button className="danger" onClick={() => run(() => gameAction(game.id, "abort", game.stateVersion))}>中止</button>
              )}
              {game.canUndo && (
                <button className="secondary" onClick={() => {
                  if (window.confirm(`${game.undoLabel ?? "直前の操作"}を取り消しますか？`) && confirmEnded()) {
                    run(() => gameAction(game.id, "undo", game.stateVersion, Boolean(isEnded)));
                  }
                }}>Undo: {game.undoLabel}</button>
              )}
              {game.canRedo && (
                <button className="secondary" onClick={() => {
                  if (window.confirm(`${game.redoLabel ?? "操作"}をやり直しますか？`) && confirmEnded()) {
                    run(() => gameAction(game.id, "redo", game.stateVersion, Boolean(isEnded)));
                  }
                }}>Redo: {game.redoLabel}</button>
              )}
            </div>
          </section>
          {game.status === "in_progress" && (
            <section className="panel controls">
              <h2>手動で出目を確定</h2>
              <div className="dice-row">
                {[1, 2, 3, 4, 5, 6].map((value) => (
                  <button key={value} onClick={() => run(() => submitRoll(game.id, value, game.stateVersion))}>{value}</button>
                ))}
              </div>
            </section>
          )}
          {(["in_progress", "paused", "completed", "aborted"] as string[]).includes(game.status) && (
            <section className="panel controls">
              <h2>位置訂正</h2>
              <div className="audit-fields">
                <input value={operator} onChange={(event) => setOperator(event.target.value)} placeholder="操作者名" />
                <input value={reason} onChange={(event) => setReason(event.target.value)} placeholder="訂正理由" />
              </div>
              <div className="correction-list">
                {game.players.map((player) => (
                  <label key={player.id}>
                    {player.displayName}
                    <input type="number" min="0" max="99" value={positions[player.id] ?? player.position}
                      onChange={(event) => setPositions((current) => ({ ...current, [player.id]: Number(event.target.value) }))} />
                    <button onClick={() => {
                      if (auditReady() && window.confirm(`${player.displayName}の位置を訂正しますか？`) && confirmEnded()) {
                        run(() => correctPosition(
                          game.id, player.id, positions[player.id] ?? player.position,
                          game.stateVersion, reason, operator, Boolean(isEnded)
                        ));
                      }
                    }}>訂正</button>
                  </label>
                ))}
              </div>
            </section>
          )}
          {game.turns.length > 0 && (
            <section className="panel controls">
              <h2>出目訂正</h2>
              <div className="audit-fields">
                <input value={operator} onChange={(event) => setOperator(event.target.value)} placeholder="操作者名" />
                <input value={reason} onChange={(event) => setReason(event.target.value)} placeholder="訂正理由" />
              </div>
              {game.turns.map((turn) => (
                <details key={turn.id}>
                  <summary>ターン {turn.number}: 出目 {turn.roll}</summary>
                  <div className="dice-row compact">
                    {[1, 2, 3, 4, 5, 6].map((value) => (
                      <button key={value} onClick={() => {
                        prepareRollCorrection(turn.id, turn.number, value);
                      }}>{value}</button>
                    ))}
                  </div>
                </details>
              ))}
            </section>
          )}
          <section className="panel controls">
            <h2>スナップショット復元</h2>
            <div className="audit-fields">
              <input type="number" min="0" max={game.stateVersion} value={restoreVersion}
                onChange={(event) => setRestoreVersion(Number(event.target.value))} />
              <button className="danger" onClick={() => {
                if (window.confirm(`状態バージョン${restoreVersion}へ復元しますか？`) && confirmEnded()) {
                  run(() => restoreGame(
                    game.id, restoreVersion, game.stateVersion, Boolean(isEnded)
                  ));
                }
              }}>復元</button>
            </div>
          </section>
        </>
      )}
      </fieldset>
      {error && <p className="error">{error}</p>}
    </main>
  );
}

function PlayerView() {
  const [game, setGame] = useState<GameState | null>(null);

  useEffect(() => {
    const update = () => getCurrentGame().then(setGame).catch(() => setGame(null));
    update();
    const timer = window.setInterval(update, 1000);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <main className="player-view">
      <p className="eyebrow">実況画面</p>
      <h1>{game ? statusLabels[game.status] : "ゲーム開始待ち"}</h1>
      {game ? <PlayerList game={game} /> : <p>管理者がゲームを準備しています。</p>}
      <section className="commentary-placeholder">実況コメントはここに表示されます</section>
    </main>
  );
}

export default function App() {
  return window.location.pathname.startsWith("/player") ? <PlayerView /> : <AdminView />;
}

