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
