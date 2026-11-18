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
  source: "manual" | "automatic" | "reviewed" | "corrected";
  confirmationId: string | null;
  originalCandidate: number | null;
  confidence: RecognitionConfidence | null;
  correctedFrom: {
    value: number;
    source: "manual" | "automatic" | "reviewed" | "corrected";
    confirmationId: string | null;
  } | null;
}

export interface RecognitionConfidence {
  detector: number;
  valueStability: number;
  stillness: number;
  positionStability: number;
  total: number;
  pipSizeConsistency: number;
  singlePipCenterOffset: number;
  pipMergeSuspected: boolean;
  pipRecounted: boolean;
  pipLayoutError: number;
}

export interface RecognitionConfirmation {
  confirmationId: string;
  value: number;
  source: "manual" | "automatic" | "reviewed" | "corrected";
  originalCandidate: number | null;
  confidence: RecognitionConfidence | null;
}

export interface RecognitionState {
  available: boolean;
  running: boolean;
  cameraConnected: boolean;
  state:
    | "unavailable"
    | "waiting"
    | "moving"
    | "stationary"
    | "candidate"
    | "confirmed"
    | "needs_confirmation"
    | "removal_wait"
    | "recognition_error"
    | "camera_error";
  candidateValue: number | null;
  candidateId: string | null;
  confidence: RecognitionConfidence | null;
  confirmation: RecognitionConfirmation | null;
  lastConfirmation: RecognitionConfirmation | null;
  errorCode: string | null;
}

export interface RecognitionCamera {
  id: number;
  label: string;
}

