import type {
  CommentaryRequestRecord,
  CommentarySettings,
  CommentaryState,
  LoggingStatus,
  SpeechSettings,
  SpeechState,
  VoiceInfo,
  SystemStatus,
  Board,
  BoardPresetList,
  BoardValidation,
  GameState,
  RecognitionCamera,
  RecognitionDiagnostics,
  RecognitionState,
  RollCorrectionPreview,
  VisionSettings,
  VisionSettingsState,
  VlmReadingRecord,
  VlmSettings
} from "./types";

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

export function getDefaultBoard(): Promise<Board> {
  return request("/api/boards/default");
}

export function listBoards(): Promise<BoardPresetList> {
  return request("/api/boards");
}

export function getBoard(boardId: string): Promise<Board> {
  return request(`/api/boards/${encodeURIComponent(boardId)}`);
}

export function validateBoard(board: Board): Promise<BoardValidation> {
  return request("/api/boards/validate", { method: "POST", body: JSON.stringify(board) });
}

export function saveBoard(board: Board): Promise<Board> {
  return request(`/api/boards/${encodeURIComponent(board.id)}`, {
    method: "PUT", body: JSON.stringify(board)
  });
}

export async function deleteBoard(boardId: string): Promise<void> {
  const response = await fetch(`/api/boards/${encodeURIComponent(boardId)}`, { method: "DELETE" });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: { message?: string } } | null;
    throw new Error(body?.detail?.message ?? `HTTP ${response.status}`);
  }
}

export interface NewPlayer {
  name: string;
  color: string;
  displayName?: string;
  spokenName?: string;
}

export function createGame(players: NewPlayer[], boardId: string): Promise<GameState> {
  return request("/api/games", {
    method: "POST",
    body: JSON.stringify({ players, boardId })
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

export function getRecognition(): Promise<RecognitionState> {
  return request("/api/recognition");
}

export function getRecognitionCameras(): Promise<{ cameras: RecognitionCamera[] }> {
  return request("/api/recognition/cameras");
}

export function startRecognition(cameraId?: number): Promise<RecognitionState> {
  return request("/api/recognition/start", {
    method: "POST",
    body: JSON.stringify({ cameraId })
  });
}

export function stopRecognition(): Promise<RecognitionState> {
  return request("/api/recognition/stop", { method: "POST" });
}

export function confirmRecognitionCandidate(
  gameId: string,
  candidateId: string,
  expectedStateVersion: number,
  expectedTurnNumber: number,
  value?: number
): Promise<GameState> {
  return request("/api/recognition/candidate/confirm", {
    method: "POST",
    body: JSON.stringify({
      gameId, candidateId, expectedStateVersion, expectedTurnNumber, value
    })
  });
}

export function rejectRecognitionCandidate(
  candidateId: string
): Promise<RecognitionState> {
  return request("/api/recognition/candidate/reject", {
    method: "POST",
    body: JSON.stringify({ candidateId })
  });
}


export function changeTurn(
  gameId: string, playerId: string, expectedStateVersion: number,
  reason: string, operator: string
): Promise<GameState> {
  return request(`/api/games/${gameId}/turn-change`, {
    method: "POST",
    body: JSON.stringify({ playerId, expectedStateVersion, reason, operator })
  });
}

export function resumeFromTurn(
  gameId: string, turnId: string | null, expectedStateVersion: number,
  reason: string, operator: string, confirmEndedGame = false
): Promise<GameState> {
  return request(`/api/games/${gameId}/resume-from-turn`, {
    method: "POST",
    body: JSON.stringify({ turnId, expectedStateVersion, reason, operator, confirmEndedGame })
  });
}

export function getSystemStatus(): Promise<SystemStatus> {
  return request("/api/status");
}

export function updateLogRetention(retention: "rotate" | "permanent"): Promise<LoggingStatus> {
  return request("/api/settings/logging", {
    method: "PUT",
    body: JSON.stringify({ retention })
  });
}

export function regenerateCommentary(turnId?: string): Promise<CommentaryState> {
  return request("/api/commentary/regenerate", {
    method: "POST", body: JSON.stringify(turnId ? { turnId } : {})
  });
}

export function getCommentaryRequests(
  gameId: string, limit = 20
): Promise<{ requests: CommentaryRequestRecord[] }> {
  const query = new URLSearchParams({ gameId, limit: String(limit) });
  return request(`/api/commentary/requests?${query}`);
}

export function getCommentarySettings(): Promise<CommentarySettings> {
  return request("/api/settings/commentary");
}

export type CommentarySettingsChange = Partial<Omit<CommentarySettings, "providers">>;

export function updateCommentarySettings(
  changes: CommentarySettingsChange
): Promise<CommentarySettings> {
  return request("/api/settings/commentary", { method: "PUT", body: JSON.stringify(changes) });
}

export function getSpeechSettings(): Promise<SpeechSettings> {
  return request("/api/settings/speech");
}

export type SpeechSettingsChange = Partial<Pick<SpeechSettings,
  "enabled" | "voice" | "rate" | "volume" | "provider" | "googleVoice" | "googleSpeakingRate"
  | "geminiBackend" | "geminiModel" | "geminiVoice" | "geminiStyle">>;

export function updateSpeechSettings(changes: SpeechSettingsChange): Promise<SpeechSettings> {
  return request("/api/settings/speech", { method: "PUT", body: JSON.stringify(changes) });
}

export function listVoices(provider?: SpeechSettings["provider"]): Promise<{ voices: VoiceInfo[] }> {
  return request(`/api/speech/voices${provider ? `?provider=${provider}` : ""}`);
}

export function previewSpeech(
  preview: SpeechSettingsChange & { text?: string }
): Promise<SpeechState> {
  return request("/api/speech/preview", { method: "POST", body: JSON.stringify(preview) });
}

export function stopSpeech(): Promise<SpeechState> {
  return request("/api/speech/stop", { method: "POST", body: "{}" });
}

// Partial update: nested roi / cameraResolution merge by key on the server.
export type VisionSettingsChange = Partial<Omit<VisionSettings, "roi" | "cameraResolution">> & {
  roi?: Partial<VisionSettings["roi"]>;
  cameraResolution?: VisionSettings["cameraResolution"];
};

export function getVisionSettings(): Promise<VisionSettingsState> {
  return request<VisionSettingsState>("/api/settings/vision");
}

export function updateVisionSettings(changes: VisionSettingsChange): Promise<VisionSettingsState> {
  return request<VisionSettingsState>("/api/settings/vision", {
    method: "PUT", body: JSON.stringify(changes)
  });
}

export function saveVisionSettings(): Promise<VisionSettingsState> {
  return request<VisionSettingsState>("/api/settings/vision/save", { method: "POST" });
}

export function revertVisionSettings(target: "saved" | "defaults"): Promise<VisionSettingsState> {
  return request<VisionSettingsState>("/api/settings/vision/revert", {
    method: "POST", body: JSON.stringify({ target })
  });
}

export function getRecognitionDiagnostics(): Promise<RecognitionDiagnostics> {
  return request<RecognitionDiagnostics>("/api/recognition/diagnostics");
}

export type VlmSettingsChange = Partial<Pick<VlmSettings,
  "readingMode" | "model" | "timeoutSeconds" | "maxRetries" | "saveImages">>;

export function getVlmSettings(): Promise<VlmSettings> {
  return request<VlmSettings>("/api/settings/vlm");
}

export function updateVlmSettings(changes: VlmSettingsChange): Promise<VlmSettings> {
  return request<VlmSettings>("/api/settings/vlm", { method: "PUT", body: JSON.stringify(changes) });
}

export function testVlmReading(): Promise<VlmReadingRecord> {
  return request<VlmReadingRecord>("/api/recognition/vlm/test", { method: "POST" });
}

export function getVlmReadings(): Promise<{ readings: VlmReadingRecord[] }> {
  return request<{ readings: VlmReadingRecord[] }>("/api/recognition/vlm/readings");
}
