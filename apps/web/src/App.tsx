import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiRequestError,
  activateGame,
  confirmRecognitionCandidate,
  gameAction,
  getCurrentGame,
  getGames,
  getRecognitionCameras,
  rejectRecognitionCandidate,
  startRecognition,
  stopRecognition,
  submitRoll
} from "./api";
import { HistoryPanel, confirmEnded, isEnded, playerName } from "./AdminPanels";
import { BoardView } from "./Board";
import { newerGame, useRealtime } from "./realtime";
import { GameSetupPanel } from "./Setup";
import { StatsPanel } from "./Stats";
import { StatusPanel } from "./StatusPanel";
import type {
  CommentaryState, GameState, RecognitionCamera, RecognitionState
} from "./types";

const statusLabels: Record<GameState["status"], string> = {
  ready: "開始待ち",
  in_progress: "進行中",
  paused: "一時停止中",
  suspended: "保存終了",
  completed: "順位確定・ゲーム終了",
  aborted: "ゲーム中止"
};

function rankText(game: GameState, player: GameState["players"][number]): string {
  if (player.rank === null) return "";
  // An aborted game never gets an official result.
  if (game.status === "aborted") return `暫定${player.rank}位`;
  return `${player.rank}位${player.rankConfirmed ? "確定" : ""}`;
}

function resumable(game: GameState): boolean {
  return ["suspended", "paused", "aborted"].includes(game.status);
}

function formatDate(value: string | null): string {
  return value ? new Date(value).toLocaleString([], { dateStyle: "short", timeStyle: "short" }) : "未開始";
}

function PastGamesDialog({
  games, onClose, onSelect
}: {
  games: GameState[];
  onClose: () => void;
  onSelect: (game: GameState) => void;
}) {
  const ordered = [...games].sort((a, b) => Number(resumable(b)) - Number(resumable(a)));
  return (
    <div className="dialog-backdrop" role="presentation" onClick={onClose}>
      <div className="dialog wide" role="dialog" aria-modal="true" aria-labelledby="past-title"
        onClick={(event) => event.stopPropagation()}>
        <h2 id="past-title">過去のゲームを選択</h2>
        <p className="muted">
          選んだゲームが現在のゲームになり、プレイヤー画面にも表示されます。
          一時停止・保存終了したゲームは「再開」、中止したゲームは「中止を取り消して再開」で続けられます。
        </p>
        {ordered.length === 0 ? <p>ほかのゲームはありません。</p> : (
          <ul className="past-games">
            {ordered.map((item) => (
              <li key={item.id}>
                <button className={resumable(item) ? "" : "secondary"} onClick={() => onSelect(item)}>
                  <span className="past-status">{statusLabels[item.status]}</span>
                  <span>
                    {item.roundNumber > 0 ? `ターン${item.roundNumber}` : "開始前"}
                    ・{item.players.map((player) => player.displayName).join(" / ")}
                  </span>
                  <span className="muted">{item.board.name}・{formatDate(item.startedAt)}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className="button-row dialog-actions">
          <button className="secondary" onClick={onClose} autoFocus>閉じる</button>
        </div>
      </div>
    </div>
  );
}

function AbortDialog({
  game, onCancel, onSuspend, onAbort
}: {
  game: GameState;
  onCancel: () => void;
  onSuspend: () => void;
  onAbort: () => void;
}) {
  const canSuspend = game.status === "in_progress" || game.status === "paused";
  return (
    <div className="dialog-backdrop" role="presentation" onClick={onCancel}>
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="abort-title"
        onClick={(event) => event.stopPropagation()}>
        <h2 id="abort-title">ゲームを終わりにしますか？</h2>
        <p>ゲームの状態は操作のたびに自動で保存されています。終わり方を選んでください。</p>
        <ul>
          {canSuspend && (
            <li><strong>保存して終了</strong>：続きを後で再開できます（「過去のゲームを選択」から開けます）。</li>
          )}
          <li>
            <strong>中止して終了</strong>：このゲームを終わりにし、今の順位を暫定順位として残します。
            あとで「中止を取り消して再開」することもできます。
          </li>
        </ul>
        <div className="button-row dialog-actions">
          <button className="secondary" onClick={onCancel} autoFocus>キャンセル</button>
          {canSuspend && <button onClick={onSuspend}>保存して終了</button>}
          <button className="danger" onClick={onAbort}>中止して終了</button>
        </div>
      </div>
    </div>
  );
}

function PlayerList({ game }: { game: GameState }) {
  return (
    <div className="players">
      {game.players.map((player) => (
        <article className={`player-card ${player.isCurrent ? "current" : ""}`} key={player.id}>
          <span className="token" style={{ backgroundColor: player.color }} />
          <div>
            <h3>{player.displayName}</h3>
            <p>{player.position}マス目 ・ {rankText(game, player)}</p>
            {player.reachedGoal && <strong>ゴール</strong>}
          </div>
        </article>
      ))}
    </div>
  );
}

const recognitionLabels: Record<RecognitionState["state"], string> = {
  unavailable: "利用不可",
  waiting: "投擲待ち",
  moving: "サイコロが動いています",
  stationary: "静止を確認中",
  candidate: "候補を判定中",
  confirmed: "確定",
  needs_confirmation: "確認が必要です",
  removal_wait: "盤上のサイコロを取り除いてから振ってください",
  recognition_error: "認識できませんでした",
  camera_error: "カメラエラー"
};

function errorMessage(reason: unknown, fallback: string): string {
  return reason instanceof Error ? reason.message : fallback;
}

function RecognitionPreview({ running }: { running: boolean }) {
  const [tick, setTick] = useState(0);
  const [visible, setVisible] = useState(true);

  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => setTick((value) => value + 1), 500);
    return () => window.clearInterval(timer);
  }, [running]);

  if (!running) return null;
  return (
    <img
      className="recognition-preview"
      src={`/api/recognition/preview.jpg?t=${tick}`}
      alt="カメラのROIプレビュー"
      style={{ visibility: visible ? "visible" : "hidden" }}
      onLoad={() => setVisible(true)}
      onError={() => setVisible(false)}
    />
  );
}

function RecognitionPanel({
  game, recognition, busy, onGame, onError
}: {
  game: GameState | null;
  recognition: RecognitionState | null;
  busy: boolean;
  onGame: (operation: () => Promise<GameState>) => void;
  onError: (message: string | null) => void;
}) {
  const [cameras, setCameras] = useState<RecognitionCamera[]>([]);
  const [cameraId, setCameraId] = useState<number | undefined>(undefined);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    if (!recognition?.available) return;
    getRecognitionCameras()
      .then(({ cameras: found }) => {
        setCameras(found);
        setCameraId((current) => current ?? found[0]?.id);
      })
      .catch(() => undefined);
  }, [recognition?.available]);

  if (!recognition) return null;
  const control = async (operation: () => Promise<unknown>, fallback: string) => {
    if (pending) return;
    setPending(true);
    try {
      onError(null);
      await operation();
    } catch (reason) {
      onError(errorMessage(reason, fallback));
    } finally {
      setPending(false);
    }
  };
  const candidateId = recognition.candidateId;
  const reviewing = recognition.state === "needs_confirmation" && candidateId && game;

  return (
    <section className="panel controls recognition-panel">
      <h2>サイコロ認識</h2>
      <p className="recognition-status">
        <strong>{recognitionLabels[recognition.state]}</strong>
        {recognition.candidateValue !== null && <span>候補 {recognition.candidateValue}</span>}
        {recognition.confidence && <span>信頼度 {recognition.confidence.total.toFixed(2)}</span>}
        {recognition.errorCode && <span className="muted">({recognition.errorCode})</span>}
      </p>
      {recognition.running && !recognition.gameReady && (
        <p className="muted">
          ゲームが進行中ではないため、サイコロは認識していません（カメラ映像の確認のみ）。
          開始または再開すると認識を始めます。そのとき盤上にあるサイコロは読み取りません。
        </p>
      )}
      {!recognition.available ? (
        <p>この環境ではカメラ認識を利用できません。手動で出目を確定してください。</p>
      ) : (
        <div className="button-row">
          {!recognition.running && (
            <>
              <select value={cameraId ?? ""} disabled={pending}
                onChange={(event) => setCameraId(Number(event.target.value))}>
                {cameras.map((camera) => (
                  <option key={camera.id} value={camera.id}>{camera.label}</option>
                ))}
              </select>
              <button disabled={pending} onClick={() => control(
                () => startRecognition(cameraId), "認識を開始できませんでした"
              )}>認識を開始</button>
            </>
          )}
          {recognition.running && (
            <button className="secondary" disabled={pending} onClick={() => control(
              stopRecognition, "認識を停止できませんでした"
            )}>認識を停止</button>
          )}
        </div>
      )}
      {reviewing && (
        <div className="recognition-review">
          <p>
            候補 {recognition.candidateValue} を確認してください。違う場合は正しい目を、
            サイコロが傾いているなどの場合は振り直しを選びます。
          </p>
          <div className="button-row">
            <button disabled={busy} onClick={() => onGame(() => confirmRecognitionCandidate(
              game.id, candidateId, game.stateVersion, game.turnNumber
            ))}>{recognition.candidateValue} で確定</button>
            <button className="secondary" disabled={pending} onClick={() => control(
              () => rejectRecognitionCandidate(candidateId), "振り直しにできませんでした"
            )}>振り直し</button>
          </div>
          <div className="dice-row compact">
            {[1, 2, 3, 4, 5, 6].map((value) => (
              <button key={value} disabled={busy} onClick={() => onGame(() => confirmRecognitionCandidate(
                game.id, candidateId, game.stateVersion, game.turnNumber, value
              ))}>{value}</button>
            ))}
          </div>
        </div>
      )}
      <RecognitionPreview running={recognition.running} />
    </section>
  );
}

function CommentaryView({
  commentary, game, large = false
}: {
  commentary: CommentaryState | null;
  game: GameState | null;
  large?: boolean;
}) {
  // Only show commentary that belongs to the game on screen.
  const current = commentary && game && commentary.gameId === game.id ? commentary : null;
  let body: string;
  if (!current || current.status === "idle") body = "実況コメントはここに表示されます";
  else if (current.status === "generating") body = `ターン${current.turnNumber}の実況を準備しています…`;
  else if (current.status === "failed") body = "実況を作成できませんでした";
  else body = current.text ?? "";
  return (
    <section className={`commentary ${large ? "large" : "panel"} ${current?.status ?? "idle"}`}
      aria-live="polite">
      {!large && <h2>実況</h2>}
      <p>{body}</p>
      {!large && current?.status === "ready" && (
        <p className="muted">ターン{current.turnNumber}・{current.provider}・{current.durationMs}ms</p>
      )}
    </section>
  );
}

function AdminView() {
  const [game, setGame] = useState<GameState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [savedGames, setSavedGames] = useState<GameState[]>([]);
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const realtime = useRealtime();
  const [setupOpen, setSetupOpen] = useState(false);
  const [abortDialog, setAbortDialog] = useState(false);
  const [pastOpen, setPastOpen] = useState(false);
  const openPastGames = () => {
    getGames().then(setSavedGames).catch(() => undefined);
    setPastOpen(true);
  };

  useEffect(() => {
    if (realtime.gameLoaded) setGame((current) => newerGame(current, realtime.game));
  }, [realtime.game, realtime.gameLoaded]);

  const [notice, setNotice] = useState<string | null>(null);
  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 6000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const run = useCallback(async (operation: () => Promise<GameState>, done?: string) => {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    try {
      setError(null);
      setNotice(null);
      const result = await operation();
      setGame((current) => newerGame(current, result));
      if (done) setNotice(done);
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

  const ended = game ? isEnded(game) : false;

  return (
    <main>
      <header>
        <div>
          <p className="eyebrow">管理者画面</p>
          <h1>Snakes & Ladders</h1>
        </div>
        <div className="header-links">
          {!realtime.connected && <span className="connection-warning">サーバーに再接続しています…</span>}
          <a href="/player" target="_blank" rel="noreferrer">プレイヤー画面を開く</a>
        </div>
      </header>

      <fieldset disabled={busy} className="operation-area">
      {pastOpen && (
        <PastGamesDialog games={savedGames.filter((item) => item.id !== game?.id)}
          onClose={() => setPastOpen(false)}
          onSelect={(item) => {
            setPastOpen(false);
            setSetupOpen(false);
            run(() => activateGame(item), "過去のゲームを開きました");
          }} />
      )}
      {!game ? (
        <>
          <div className="button-row past-games-entry">
            <button className="secondary" onClick={openPastGames}>過去のゲームを選択</button>
          </div>
          <GameSetupPanel onCreate={run} />
        </>
      ) : (
        <>
          <section className="status-bar">
            <span>{statusLabels[game.status]}</span>
            {game.currentPlayerId && <span>手番: {playerName(game, game.currentPlayerId)}</span>}
            {game.roundNumber > 0 && <span>ターン {game.roundNumber}</span>}
          </section>
          {game.pauseReason === "after_correction" && (
            <section className="interruption-banner">
              <strong>訂正したため一時停止しています。</strong>
              <span>盤上のコマを画面の位置に合わせてから「再開」を押してください。</span>
            </section>
          )}
          {game.status === "aborted" && (
            <section className="interruption-banner">
              <strong>このゲームは中止されました。</strong>
              <span>順位は暫定です。続ける場合は「中止を取り消して再開」を押してください。</span>
            </section>
          )}
          {abortDialog && (
            <AbortDialog game={game}
              onCancel={() => setAbortDialog(false)}
              onSuspend={() => {
                setAbortDialog(false);
                run(() => gameAction(game.id, "suspend", game.stateVersion),
                  "保存して終了しました。「過去のゲームを選択」から続けられます");
              }}
              onAbort={() => {
                setAbortDialog(false);
                run(() => gameAction(game.id, "abort", game.stateVersion),
                  "ゲームを中止しました（順位は暫定）");
              }} />
          )}
          {game.interruption && (
            <section className="interruption-banner">
              <strong>前回は異常終了しました。</strong>
              <span>ターン{game.roundNumber}・実物の盤面と画面の位置を確認してから再開してください。</span>
            </section>
          )}
          <PlayerList game={game} />
          {setupOpen && (
            <GameSetupPanel
              onCreate={(operation) => { setSetupOpen(false); run(operation); }}
              onCancel={() => setSetupOpen(false)} />
          )}
          <BoardView game={game} />
          <CommentaryView commentary={realtime.commentary} game={game} />
          <RecognitionPanel game={game} recognition={realtime.recognition} busy={busy}
            onGame={run} onError={setError} />
          <section className="panel controls">
            <h2>ゲーム操作</h2>
            <div className="button-row">
              {(game.status === "completed" || game.status === "aborted") && (
                <button onClick={() => setSetupOpen(true)}>新しいゲーム</button>
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
                <button className="danger" onClick={() => setAbortDialog(true)}>中止</button>
              )}
              <button className="secondary" onClick={openPastGames}>過去のゲームを選択</button>
              {game.status === "aborted" && (
                <button onClick={() => {
                  if (window.confirm("中止を取り消して、中止する前の状態からゲームを続けますか？")) {
                    run(() => gameAction(game.id, "reopen", game.stateVersion), "中止を取り消しました");
                  }
                }}>中止を取り消して再開</button>
              )}
              {game.canUndo && (
                <button className="secondary" onClick={() => {
                  if (window.confirm(`${game.undoLabel ?? "直前の操作"}を取り消しますか？`) && confirmEnded(game)) {
                    run(() => gameAction(game.id, "undo", game.stateVersion, ended),
                      `取り消しました: ${game.undoLabel ?? "直前の操作"}`);
                  }
                }}>Undo: {game.undoLabel}</button>
              )}
              {game.canRedo && (
                <button className="secondary" onClick={() => {
                  if (window.confirm(`${game.redoLabel ?? "操作"}をやり直しますか？`) && confirmEnded(game)) {
                    run(() => gameAction(game.id, "redo", game.stateVersion, ended),
                      `やり直しました: ${game.redoLabel ?? "操作"}`);
                  }
                }}>Redo: {game.redoLabel}</button>
              )}
            </div>
          </section>
          {game.status === "in_progress" && (
            <section className="panel controls">
              <h2>手動で出目を確定</h2>
              <p className="muted">
                {game.currentPlayerId ? `${playerName(game, game.currentPlayerId)}の出目` : ""}
              </p>
              <div className="dice-row">
                {[1, 2, 3, 4, 5, 6].map((value) => (
                  <button key={value} onClick={() => run(() => submitRoll(game.id, value, game.stateVersion))}>{value}</button>
                ))}
              </div>
            </section>
          )}
          <StatsPanel game={game} />
          <HistoryPanel game={game} run={run} onError={setError} />
        </>
      )}
      </fieldset>
      <StatusPanel connected={realtime.connected} />
      {notice && <p className="notice toast" role="status">{notice}</p>}
      {error && <p className="error">{error}</p>}
    </main>
  );
}

function PlayerView() {
  const { game, recognition, commentary, connected } = useRealtime();
  const recognizing = recognition?.running && game?.status === "in_progress"
    && ["moving", "stationary", "candidate", "needs_confirmation"].includes(recognition.state);

  return (
    <main className="player-view">
      <p className="eyebrow">実況画面</p>
      <h1>{game ? statusLabels[game.status] : "ゲーム開始待ち"}</h1>
      {recognizing && <p className="recognition-status">サイコロを読み取っています…</p>}
      {!connected && <p className="connection-warning">サーバーに再接続しています…</p>}
      {game ? <PlayerList game={game} /> : <p>管理者がゲームを準備しています。</p>}
      <CommentaryView commentary={commentary} game={game} large />
    </main>
  );
}

export default function App() {
  return window.location.pathname.startsWith("/player") ? <PlayerView /> : <AdminView />;
}

