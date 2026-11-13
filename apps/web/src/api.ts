import type { GameState } from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers }
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: string | { message?: string }
    } | null;
    const detail = body?.detail;
    throw new Error(
      typeof detail === "string" ? detail : detail?.message ?? `HTTP ${response.status}`
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

