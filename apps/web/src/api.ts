import type { GameState } from "./types";

interface ApiErrorDetail {
  code: string;
  message: string;
  context: Record<string, unknown>;
  requestId: string;
}

export class ApiRequestError extends Error {
  constructor(
    message: string,
    readonly code: string,
    readonly context: Record<string, unknown>,
    readonly requestId: string
  ) {
    super(message);
    this.name = "ApiRequestError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers }
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: string | Partial<ApiErrorDetail>
    } | null;
    const detail = body?.detail;
    if (detail && typeof detail !== "string") {
      throw new ApiRequestError(
        detail.message ?? `HTTP ${response.status}`,
        detail.code ?? "http_error",
        detail.context ?? {},
        detail.requestId ?? "unknown"
      );
    }
    throw new ApiRequestError(
      typeof detail === "string" ? detail : `HTTP ${response.status}`,
      "http_error", {}, "unknown"
    );
  }
  return response.json() as Promise<T>;
}

export function createDefaultGame(): Promise<GameState> {
  return request("/api/games", {
    method: "POST",
    body: JSON.stringify({
      players: [
        { name: "プレイヤー1", color: "red" },
        { name: "プレイヤー2", color: "blue" }
      ]
    })
  });
}

export function getCurrentGame(): Promise<GameState> {
  return request("/api/games/current");
}

export function getGames(): Promise<GameState[]> {
  return request("/api/games");
}

export function gameAction(
  gameId: string, action: string, expectedStateVersion: number,
  confirmEndedGame = false
): Promise<GameState> {
  return request(`/api/games/${gameId}/${action}`, {
    method: "POST",
    body: JSON.stringify({ expectedStateVersion, confirmEndedGame })
  });
}

export function activateGame(game: GameState): Promise<GameState> {
  return request(`/api/games/${game.id}/activate`, {
    method: "POST",
    body: JSON.stringify({ expectedStateVersion: game.stateVersion })
  });
}

export function submitRoll(
  gameId: string, value: number, expectedStateVersion: number
): Promise<GameState> {
  return request(`/api/games/${gameId}/rolls`, {
    method: "POST",
    body: JSON.stringify({ value, expectedStateVersion })
  });
}

export function correctPosition(
  gameId: string, playerId: string, position: number, expectedStateVersion: number,
  reason: string, operator: string, confirmEndedGame = false
): Promise<GameState> {
  return request(`/api/games/${gameId}/position-corrections`, {
    method: "POST",
    body: JSON.stringify({
      playerId, position, expectedStateVersion, reason, operator, confirmEndedGame
    })
  });
}

export function correctRoll(
  gameId: string, turnId: string, value: number, expectedStateVersion: number,
  reason: string, operator: string, confirmEndedGame = false,
  confirmInvalidatedRolls = false
): Promise<GameState> {
  return request(`/api/games/${gameId}/turns/${turnId}/correction`, {
    method: "POST",
    body: JSON.stringify({
      value, expectedStateVersion, reason, operator, confirmEndedGame,
      confirmInvalidatedRolls
    })
  });
}

export interface RollCorrectionPreview {
  requiresConfirmation: boolean;
  invalidatedTurns: Array<{
    eventId: string;
    turnId: string;
    turnNumber: number;
    playerId: string;
    value: number;
    reason: "player_already_finished" | "game_completed";
  }>;
  invalidatedOperations: Array<{
    eventId: string;
    timelineIndex: number;
    type: "start" | "pause" | "resume" | "suspend" | "abort" | "position_correction";
    reason: "player_already_finished" | "game_completed";
    playerId?: string;
    position?: number;
  }>;
}

export function previewRollCorrection(
  gameId: string, turnId: string, value: number, expectedStateVersion: number,
  reason: string, operator: string, confirmEndedGame = false
): Promise<RollCorrectionPreview> {
  return request(`/api/games/${gameId}/turns/${turnId}/correction`, {
    method: "POST",
    body: JSON.stringify({
      value, expectedStateVersion, reason, operator, confirmEndedGame, dryRun: true
    })
  });
}

export function restoreGame(
  gameId: string, targetStateVersion: number, expectedStateVersion: number,
  confirmEndedGame = false
): Promise<GameState> {
  return request(`/api/games/${gameId}/restore`, {
    method: "POST",
    body: JSON.stringify({ targetStateVersion, expectedStateVersion, confirmEndedGame })
  });
}

