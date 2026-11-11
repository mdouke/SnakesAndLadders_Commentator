export type GameStatus =
  | "ready"
  | "in_progress"
  | "paused"
  | "suspended"
  | "completed"
  | "aborted";

export interface PlayerState {
  id: string;
  displayName: string;
  color: string;
  position: number;
  rank: number | null;
  rankConfirmed: boolean;
  reachedGoal: boolean;
  remainingSquares: number;
  isCurrent: boolean;
}

export interface GameState {
  id: string;
  status: GameStatus;
  turnNumber: number;
  currentPlayerId: string | null;
  players: PlayerState[];
}

