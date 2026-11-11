import { useCallback, useEffect, useRef, useState } from "react";

import { createDefaultGame, gameAction, getCurrentGame, submitRoll } from "./api";
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
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);

  const run = useCallback(async (operation: () => Promise<GameState>) => {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    try {
      setError(null);
      setGame(await operation());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "操作に失敗しました");
      await getCurrentGame().then(setGame).catch(() => undefined);
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    getCurrentGame().then(setGame).catch(() => undefined);
  }, []);

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
          <PlayerList game={game} />
          <section className="panel controls">
            <h2>ゲーム操作</h2>
            <div className="button-row">
              {(game.status === "completed" || game.status === "aborted") && (
                <button onClick={() => run(createDefaultGame)}>新しいゲーム</button>
              )}
              {game.status === "ready" && (
                <button onClick={() => run(() => gameAction(game.id, "start"))}>開始</button>
              )}
              {game.status === "in_progress" && (
                <>
                  <button className="secondary" onClick={() => run(() => gameAction(game.id, "pause"))}>一時停止</button>
                  <button className="secondary" onClick={() => run(() => gameAction(game.id, "suspend"))}>保存して終了</button>
                </>
              )}
              {(game.status === "paused" || game.status === "suspended") && (
                <button onClick={() => run(() => gameAction(game.id, "resume"))}>再開</button>
              )}
              {!(["completed", "aborted"] as string[]).includes(game.status) && (
                <button className="danger" onClick={() => run(() => gameAction(game.id, "abort"))}>中止</button>
              )}
            </div>
          </section>
          {game.status === "in_progress" && (
            <section className="panel controls">
              <h2>手動で出目を確定</h2>
              <div className="dice-row">
                {[1, 2, 3, 4, 5, 6].map((value) => (
                  <button key={value} onClick={() => run(() => submitRoll(game.id, value, game.turnNumber))}>{value}</button>
                ))}
              </div>
            </section>
          )}
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

