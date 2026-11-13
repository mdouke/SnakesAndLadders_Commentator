export type GameStatus =
  | "ready"
  | "in_progress"
  | "paused"
  | "suspended"
  | "completed"
  | "aborted";

export interface PlayerState {
  winningRolls: number[];
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
  branchId: string;
  stateVersion: number;
  canUndo: boolean;
  canRedo: boolean;
  undoLabel: string | null;
  redoLabel: string | null;
  interruption: { detectedAt: string; lastSavedAt: string | null } | null;
  status: GameStatus;
  turnNumber: number;
  currentPlayerId: string | null;
  players: PlayerState[];
  turns: TurnRecord[];
}

export interface TurnRecord {
  id: string;
  number: number;
  playerId: string;
  roll: number;
  before: number;
  finalPosition: number;
}

