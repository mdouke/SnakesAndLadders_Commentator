import { useState } from "react";

import type { GameState, TurnRecord } from "./types";

type Player = GameState["players"][number];

function playerTurns(game: GameState, player: Player): TurnRecord[] {
  return game.turns.filter((turn) => turn.playerId === player.id);
}

function jumpText(turn: TurnRecord): string {
  return `ターン${turn.round}: ${turn.normalPosition}→${turn.finalPosition}`;
}

export function StatsPanel({ game }: { game: GameState }) {
  if (game.turns.length === 0) return null;
  return (
    <section className="panel stats">
      <h2>プレイヤー情報と統計</h2>
      <div className="stats-grid">
        {game.players.map((player) => {
          const stats = player.statistics;
          const turns = playerTurns(game, player);
          const ladders = turns.filter((turn) => turn.jumpType === "ladder");
          const snakes = turns.filter((turn) => turn.jumpType === "snake");
          return (
            <article className="stats-card" key={player.id}>
              <h3><span className="token small-token" style={{ backgroundColor: player.color }} />{player.displayName}</h3>
              <dl>
                <dt>位置</dt><dd>{player.position}マス目（残り{player.remainingSquares}）</dd>
                <dt>最高到達</dt><dd>{stats.maximumPosition}マス目</dd>
                <dt>出目</dt><dd>{stats.rolls.length}回・平均{stats.averageRoll.toFixed(2)}</dd>
                <dt>出目の分布</dt>
                <dd className="face-counts">
                  {["1", "2", "3", "4", "5", "6"].map((face) => (
                    <span key={face}><b>{face}</b>{stats.faceCounts[face] ?? 0}</span>
                  ))}
                </dd>
                <dt>ハシゴ</dt><dd>{stats.ladderCount}回・計+{stats.ladderDistanceTotal}マス</dd>
                <dt>ヘビ</dt><dd>{stats.snakeCount}回・計−{stats.snakeDistanceTotal}マス</dd>
                <dt>ゴール超過</dt><dd>{stats.overshootCount}回</dd>
                {!player.reachedGoal && (
                  <>
                    <dt>ゴールまで</dt><dd>最短{player.theoreticalMinimumRolls}回</dd>
                    {player.winningRolls.length > 0 && (
                      <><dt>次にゴールできる目</dt><dd>{player.winningRolls.join("・")}</dd></>
                    )}
                  </>
                )}
              </dl>
              <details>
                <summary>出目・ヘビ・ハシゴの履歴</summary>
                <p className="muted">出目: {stats.rolls.join(", ") || "なし"}</p>
                {ladders.length > 0 && <p className="muted">ハシゴ: {ladders.map(jumpText).join("、")}</p>}
                {snakes.length > 0 && <p className="muted">ヘビ: {snakes.map(jumpText).join("、")}</p>}
              </details>
            </article>
          );
        })}
      </div>
      <PositionChart game={game} />
    </section>
  );
}

const WIDTH = 640;
const HEIGHT = 300;
const MARGIN = { top: 12, right: 104, bottom: 30, left: 36 };
const LABEL_GAP = 13;

interface Point {
  x: number;
  y: number;
  turn: TurnRecord | null;
}

/** Spread end labels apart vertically so they never overlap. */
function spreadLabels(ys: number[], min: number, max: number): number[] {
  const order = ys.map((value, index) => ({ value, index })).sort((a, b) => a.value - b.value);
  const placed: number[] = [];
  for (const item of order) {
    const previous = placed.length > 0 ? placed[placed.length - 1] : -Infinity;
    placed.push(Math.max(item.value, previous + LABEL_GAP, min));
  }
  // If the stack runs past the bottom, shift it back up.
  const overflow = placed.length > 0 ? placed[placed.length - 1] - max : 0;
  const shifted = overflow > 0 ? placed.map((value) => value - overflow) : placed;
  const result = new Array<number>(ys.length);
  order.forEach((item, position) => { result[item.index] = shifted[position]; });
  return result;
}

/**
 * Position after each display turn (round). All players' points for the same turn
 * share one x position; ladders/snakes and corrected turns get distinct markers.
 */
export function PositionChart({ game }: { game: GameState }) {
  const [hover, setHover] = useState<number | null>(null);
  const lastRound = game.turns.reduce((max, turn) => Math.max(max, turn.round), 0);
  const plotWidth = WIDTH - MARGIN.left - MARGIN.right;
  const plotHeight = HEIGHT - MARGIN.top - MARGIN.bottom;
  const x = (round: number) => MARGIN.left + (lastRound === 0 ? 0 : (round / lastRound) * plotWidth);
  const y = (position: number) => MARGIN.top + plotHeight - (position / game.board.goal) * plotHeight;

  const series = game.players.map((player) => {
    const points: Point[] = [{ x: 0, y: 0, turn: null }];
    for (const turn of playerTurns(game, player)) {
      // Draw the normal move first and the jump as its own vertical segment,
      // so rises (ladders) and drops (snakes) read as steps.
      if (turn.normalPosition !== turn.finalPosition) {
        points.push({ x: turn.round, y: turn.normalPosition, turn: null });
      }
      points.push({ x: turn.round, y: turn.finalPosition, turn });
    }
    return { player, points };
  });

  const ends = series.map(({ points }) => points[points.length - 1]);
  const labelYs = spreadLabels(
    ends.map((point) => y(point.y) + 4), MARGIN.top + 8, MARGIN.top + plotHeight + 4
  );

  const hoveredTurns = hover === null ? [] : game.turns.filter((turn) => turn.round === hover);
  const ticks = [0, 25, 50, 75, 100];
  const step = Math.max(1, Math.ceil(lastRound / 8));
  const xTicks = Array.from({ length: lastRound }, (_, index) => index + 1)
    .filter((round) => round === 1 || round === lastRound || round % step === 0);
  const columnWidth = plotWidth / Math.max(lastRound, 1);

  return (
    <figure className="position-chart">
      <figcaption>
        <strong>位置の推移</strong>
        <span className="chart-legend">
          {game.players.map((player) => (
            <span key={player.id}><i style={{ backgroundColor: player.color }} />{player.displayName}</span>
          ))}
          <span><svg width="12" height="12" aria-hidden="true"><path d="M6 1 L11 11 L1 11 Z" className="marker-key" /></svg>ハシゴ</span>
          <span><svg width="12" height="12" aria-hidden="true"><path d="M1 1 L11 1 L6 11 Z" className="marker-key" /></svg>ヘビ</span>
          <span><svg width="12" height="12" aria-hidden="true"><circle cx="6" cy="6" r="4" className="marker-key hollow" /></svg>訂正済み</span>
        </span>
      </figcaption>
      <div className="chart-wrap">
        <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img"
          aria-label="各プレイヤーのターンごとの位置の推移。詳しい値は履歴の表を参照"
          onMouseLeave={() => setHover(null)}>
          {ticks.map((tick) => (
            <g key={tick}>
              <line className="grid" x1={MARGIN.left} x2={MARGIN.left + plotWidth} y1={y(tick)} y2={y(tick)} />
              <text className="axis-label" x={MARGIN.left - 6} y={y(tick) + 4} textAnchor="end">{tick}</text>
            </g>
          ))}
          {xTicks.map((round) => (
            <text key={round} className="axis-label" x={x(round)} y={HEIGHT - 10} textAnchor="middle">
              ターン{round}
            </text>
          ))}
          {hover !== null && (
            <line className="crosshair" x1={x(hover)} x2={x(hover)} y1={MARGIN.top} y2={MARGIN.top + plotHeight} />
          )}
          {series.map(({ player, points }, seriesIndex) => (
            <g key={player.id}>
              <polyline className="series-line" style={{ stroke: player.color }}
                points={points.map((point) => `${x(point.x)},${y(point.y)}`).join(" ")} />
              {points.map((point, index) => {
                const turn = point.turn;
                if (!turn) return null;
                const cx = x(point.x);
                const cy = y(point.y);
                if (turn.jumpType === "ladder") {
                  return <path key={index} className="marker" style={{ fill: player.color }}
                    d={`M${cx} ${cy - 6} L${cx + 5.5} ${cy + 4} L${cx - 5.5} ${cy + 4} Z`} />;
                }
                if (turn.jumpType === "snake") {
                  return <path key={index} className="marker" style={{ fill: player.color }}
                    d={`M${cx - 5.5} ${cy - 4} L${cx + 5.5} ${cy - 4} L${cx} ${cy + 6} Z`} />;
                }
                if (turn.correctedFrom !== null) {
                  return <circle key={index} className="marker hollow" style={{ stroke: player.color }}
                    cx={cx} cy={cy} r={4.5} />;
                }
                return null;
              })}
              {/* Direct label at the line end: identity never relies on color alone. */}
              <text className="series-label" x={x(ends[seriesIndex].x) + 8} y={labelYs[seriesIndex]}>
                {player.displayName} {player.position}
              </text>
            </g>
          ))}
          {Array.from({ length: lastRound }, (_, index) => index + 1).map((round) => (
            <rect key={round} className="hit" x={x(round) - columnWidth / 2}
              y={MARGIN.top} width={columnWidth} height={plotHeight}
              onMouseEnter={() => setHover(round)} />
          ))}
        </svg>
        {hover !== null && hoveredTurns.length > 0 && (
          <div className="chart-tooltip" style={{
            left: `${(x(hover) / WIDTH) * 100}%`,
            // Keep the tooltip inside the chart near the left and right edges.
            transform: x(hover) / WIDTH > 0.65 ? "translateX(-100%)"
              : x(hover) / WIDTH < 0.3 ? "translateX(0)" : "translateX(-50%)"
          }}>
            <strong>ターン{hover}</strong>
            {hoveredTurns.map((turn) => (
              <span key={turn.id}>
                {game.players.find((player) => player.id === turn.playerId)?.displayName}：出目{turn.roll}
                {"　"}{turn.before}→{turn.normalPosition}
                {turn.finalPosition !== turn.normalPosition && `→${turn.finalPosition}`}
                {turn.jumpType === "ladder" && "（ハシゴ）"}
                {turn.jumpType === "snake" && "（ヘビ）"}
                {turn.correctedFrom && `（訂正済み・元の出目${turn.correctedFrom.value}）`}
              </span>
            ))}
          </div>
        )}
      </div>
    </figure>
  );
}
