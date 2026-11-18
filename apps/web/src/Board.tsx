import { useEffect, useState } from "react";

import { getDefaultBoard } from "./api";
import type { Board, GameState } from "./types";

const SIZE = 10;
const CELL = 10;

/** Cell center in a 100x100 viewBox. Square 1 is bottom-left; rows alternate direction. */
function cellCenter(square: number): { x: number; y: number } {
  const index = square - 1;
  const row = Math.floor(index / SIZE);
  const offset = index % SIZE;
  const column = row % 2 === 0 ? offset : SIZE - 1 - offset;
  return { x: column * CELL + CELL / 2, y: (SIZE - 1 - row) * CELL + CELL / 2 };
}

function snakePath(from: number, to: number): string {
  const start = cellCenter(from);
  const end = cellCenter(to);
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  // Two control points pushed sideways give a simple S-curve.
  const nx = -dy * 0.25;
  const ny = dx * 0.25;
  return `M ${start.x} ${start.y} C ${start.x + dx / 3 + nx} ${start.y + dy / 3 + ny}, `
    + `${start.x + (2 * dx) / 3 - nx} ${start.y + (2 * dy) / 3 - ny}, ${end.x} ${end.y}`;
}

export function useDefaultBoard(): Board | null {
  const [board, setBoard] = useState<Board | null>(null);
  useEffect(() => {
    getDefaultBoard().then(setBoard).catch(() => setBoard(null));
  }, []);
  return board;
}

export function BoardView({ board, game }: { board: Board; game: GameState }) {
  const lastTurn = game.turns.length > 0 ? game.turns[game.turns.length - 1] : null;
  const squares = Array.from({ length: SIZE * SIZE }, (_, index) => index + 1);
  const waiting = game.players.filter((player) => player.position === 0);

  return (
    <section className="panel board-panel">
      <h2>盤面</h2>
      <svg className="board" viewBox="0 0 100 100" role="img"
        aria-label="ヘビとハシゴの盤面と各プレイヤーのコマ">
        {squares.map((square) => {
          const { x, y } = cellCenter(square);
          const isLastFrom = lastTurn?.before === square;
          const isLastTo = lastTurn?.finalPosition === square;
          return (
            <g key={square}>
              <rect x={x - CELL / 2} y={y - CELL / 2} width={CELL} height={CELL}
                className={`cell ${square % 2 === 0 ? "even" : "odd"}`
                  + `${isLastFrom ? " last-from" : ""}${isLastTo ? " last-to" : ""}`} />
              <text x={x - CELL / 2 + 0.8} y={y - CELL / 2 + 2.6} className="cell-number">
                {square}
              </text>
            </g>
          );
        })}
        {board.jumps.map((jump) => {
          if (jump.type === "ladder") {
            const start = cellCenter(jump.from);
            const end = cellCenter(jump.to);
            return (
              <line key={`l-${jump.from}`} className="ladder"
                x1={start.x} y1={start.y} x2={end.x} y2={end.y} />
            );
          }
          return <path key={`s-${jump.from}`} className="snake" d={snakePath(jump.from, jump.to)} />;
        })}
        {game.players.map((player, index) => {
          if (player.position === 0) return null;
          const { x, y } = cellCenter(player.position);
          const shift = (index - (game.players.length - 1) / 2) * 2.4;
          return (
            <circle key={player.id} cx={x + shift} cy={y + 1.5} r={1.9}
              className={`board-token${player.isCurrent ? " current" : ""}`}
              style={{ fill: player.color }}>
              <title>{`${player.displayName}: ${player.position}マス目`}</title>
            </circle>
          );
        })}
      </svg>
      <div className="board-legend">
        {waiting.length > 0 && (
          <span>スタート前: {waiting.map((player) => player.displayName).join("、")}</span>
        )}
        {lastTurn && (
          <span>
            直前: {game.players.find((player) => player.id === lastTurn.playerId)?.displayName}
            {" "}出目{lastTurn.roll}・{lastTurn.before}→{lastTurn.normalPosition}
            {lastTurn.finalPosition !== lastTurn.normalPosition && `→${lastTurn.finalPosition}`}
          </span>
        )}
        <span className="legend-ladder">ハシゴ</span>
        <span className="legend-snake">ヘビ</span>
      </div>
    </section>
  );
}
