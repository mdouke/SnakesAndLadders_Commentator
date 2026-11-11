import type { GameState } from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers }
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(body?.detail ?? `HTTP ${response.status}`);
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

export function gameAction(gameId: string, action: string): Promise<GameState> {
  return request(`/api/games/${gameId}/${action}`, { method: "POST" });
}

export function submitRoll(gameId: string, value: number): Promise<GameState> {
  return request(`/api/games/${gameId}/rolls`, {
    method: "POST",
    body: JSON.stringify({ value })
  });
}

