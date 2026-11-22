// Public API types, generated from the server's OpenAPI schema.
// Regenerate after changing an endpoint or response model:
//   .venv/bin/python -m commentator.tools.export_openapi
//   npm run generate:api
import type { components } from "./generated/api";

type Schemas = components["schemas"];

export type Board = Schemas["BoardResponse"];
export type BoardPresetList = Schemas["BoardPresetList"];
export type BoardPresetSummary = Schemas["BoardPresetSummary"];
export type BoardValidation = Schemas["BoardValidation"];
export type GameState = Schemas["GameResponse"];
export type GameStatus = GameState["status"];
export type PlayerState = Schemas["PlayerResponse"];
export type TurnRecord = Schemas["TurnResponse"];
export type RollCorrectionPreview = Schemas["RollCorrectionPreview"];
export type RecognitionConfidence = Schemas["RecognitionConfidence"];
export type RecognitionConfirmation = Schemas["RecognitionConfirmation"];
export type RecognitionState = Schemas["RecognitionResponse"];
export type RecognitionCamera = Schemas["RecognitionCamera"];
export type CommentaryState = Schemas["CommentaryResponse"];
export type RealtimeMessage = Schemas["RealtimeMessage"];
export type SystemStatus = Schemas["SystemStatus"];
export type LoggingStatus = Schemas["LoggingStatus"];
export type ComponentStatus = Schemas["ComponentStatus"];
export type TimelineEntry = Schemas["TimelineEntry"];
export type AuditEntry = Schemas["AuditEntry"];
export type SpeechState = Schemas["SpeechResponse"];
export type CommentarySettings = Schemas["CommentarySettingsResponse"];
export type CommentaryRequestRecord = Schemas["CommentaryRequestRecord"];
export type CommentaryPromptPreview = Schemas["CommentaryPromptPreview"];
export type SpeechSettings = Schemas["SpeechSettingsResponse"];
export type VoiceInfo = Schemas["VoiceInfo"];
export type VisionSettings = Schemas["VisionSettingsModel"];
export type VisionSettingsState = Schemas["VisionSettingsResponse"];
export type RecognitionDiagnostics = Schemas["RecognitionDiagnostics"];
export type VlmSettings = Schemas["VlmSettingsResponse"];
export type VlmReadingRecord = Schemas["VlmReadingRecord"];
export type RecognitionInfo = Schemas["RecognitionInfo"];
export type ReadingMode = VlmSettings["readingMode"];
