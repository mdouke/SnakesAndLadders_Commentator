"""Public response models. They define the OpenAPI schema used to generate web types.

Domain objects still serialize themselves with ``to_dict()``; FastAPI validates that
output against these models, so a key added on one side but not the other fails the
API tests (``extra="forbid"``).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, TypeAdapter
from pydantic.alias_generators import to_camel


class ResponseModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="forbid"
    )


GameStatusValue = Literal[
    "ready", "in_progress", "paused", "suspended", "completed", "aborted"
]
RollSourceValue = Literal["manual", "automatic", "reviewed", "corrected"]
RecognitionStateValue = Literal[
    "unavailable",
    "waiting",
    "moving",
    "stationary",
    "reading",
    "candidate",
    "confirmed",
    "needs_confirmation",
    "removal_wait",
    "recognition_error",
    "camera_error",
]


class HealthResponse(ResponseModel):
    status: Literal["ok"]


class BoardJump(ResponseModel):
    type: Literal["ladder", "snake"]
    from_: int = Field(alias="from")
    to: int


class BoardResponse(ResponseModel):
    id: str
    name: str
    goal: int
    jumps: list[BoardJump]


class BoardPresetSummary(ResponseModel):
    id: str
    name: str
    built_in: bool
    snake_count: int
    ladder_count: int


class BoardPresetList(ResponseModel):
    presets: list[BoardPresetSummary]


class BoardValidation(ResponseModel):
    valid: bool
    message: str | None


class PlayerStatistics(ResponseModel):
    rolls: list[int]
    face_counts: dict[str, int]
    average_roll: float
    normal_movement_total: int
    actual_position_change_total: int
    overshoot_count: int
    ladder_count: int
    ladder_distance_total: int
    snake_count: int
    snake_distance_total: int
    maximum_position: int


class PlayerResponse(ResponseModel):
    id: str
    name: str
    display_name: str
    spoken_name: str
    color: str
    position: int
    rank: int | None
    rank_confirmed: bool
    reached_goal: bool
    reached_goal_at: str | None
    remaining_squares: int
    winning_rolls: list[int]
    is_current: bool
    theoretical_minimum_rolls: int
    statistics: PlayerStatistics


class RecognitionConfidence(ResponseModel):
    detector: float
    value_stability: float
    stillness: float
    position_stability: float
    total: float
    pip_size_consistency: float
    single_pip_center_offset: float
    pip_merge_suspected: bool
    pip_recounted: bool
    pip_layout_error: float


class VlmAnswer(ResponseModel):
    dice_count: int
    value: int | None = Field(description="null: the top face could not be read")
    confidence: Literal["high", "medium", "low"]
    note: str
    model: str
    duration_ms: int


class RecognitionInfo(ResponseModel):
    """How a recognized value was read (ADR 0006)."""

    recognizer: Literal["opencv", "vlm", "hybrid"]
    reading_id: str | None
    opencv_candidate: int | None
    vlm: VlmAnswer | None
    vlm_error: str | None


class CorrectedFrom(ResponseModel):
    value: int
    source: RollSourceValue
    confirmation_id: str | None
    reason: str | None
    operator: str | None


class TurnResponse(ResponseModel):
    id: str
    number: int
    player_id: str
    roll: int
    before: int
    normal_position: int
    final_position: int
    overshoot: bool
    jump_type: Literal["ladder", "snake"] | None
    events: list[str]
    occurred_at: str
    source: RollSourceValue
    confirmation_id: str | None
    original_candidate: int | None
    confidence: RecognitionConfidence | None
    recognition: RecognitionInfo | None
    corrected_from: CorrectedFrom | None
    round: int = Field(description="Display turn: increases after every player has rolled once.")
    branch_id: str
    state_version_before: int
    state_version_after: int


class TimelineEntry(ResponseModel):
    """One event of the effective history (``turnId`` links a roll to its turn)."""

    id: str
    type: Literal[
        "start", "pause", "resume", "suspend", "abort", "reopen",
        "roll", "position_correction", "turn_change",
    ]
    occurred_at: str
    turn_id: str | None
    player_id: str | None
    previous_player_id: str | None
    before_position: int | None
    position: int | None
    reason: str | None
    operator: str | None


class AuditEntry(ResponseModel):
    """A change to the history itself; newest first."""

    id: str
    type: Literal["correction", "restore", "undo", "redo"]
    occurred_at: str
    label: str
    reason: str | None
    operator: str | None


class Interruption(ResponseModel):
    detected_at: str
    last_saved_at: str | None


class GameResponse(ResponseModel):
    id: str
    board_id: str
    board: BoardResponse
    branch_id: str
    state_version: int
    can_undo: bool
    can_redo: bool
    undo_label: str | None
    redo_label: str | None
    interruption: Interruption | None
    suspension_history: list[str]
    status: GameStatusValue
    turn_number: int = Field(description="Number of rolls so far (used for conflict checks).")
    round_number: int = Field(description="Display turn of the next roll (0 before start).")
    pause_reason: str | None = Field(
        description=(
            "Why a paused game is paused: after_correction, process_interrupted, "
            "or null for a manual pause."
        )
    )
    current_player_id: str | None
    started_at: str | None
    ended_at: str | None
    suspended_at: str | None
    players: list[PlayerResponse]
    turns: list[TurnResponse]
    timeline: list[TimelineEntry]
    audit_log: list[AuditEntry]


InvalidationReason = Literal[
    "player_already_finished", "game_completed", "turn_change_not_applicable"
]


class InvalidatedTurn(ResponseModel):
    event_id: str
    turn_id: str | None
    turn_number: int | None
    player_id: str
    value: int
    reason: InvalidationReason


class InvalidatedOperation(ResponseModel):
    event_id: str
    timeline_index: int | None
    type: str
    reason: InvalidationReason
    player_id: str | None = None
    position: int | None = None


class RollCorrectionPreview(ResponseModel):
    requires_confirmation: bool
    invalidated_turns: list[InvalidatedTurn]
    invalidated_operations: list[InvalidatedOperation]


class RecognitionConfirmation(ResponseModel):
    confirmation_id: str
    value: int
    source: RollSourceValue
    original_candidate: int | None
    confidence: RecognitionConfidence | None
    recognition: RecognitionInfo | None


class RecognitionResponse(ResponseModel):
    available: bool
    running: bool
    camera_connected: bool
    game_ready: bool = Field(
        description="False while no game is in progress: frames feed only the preview."
    )
    state: RecognitionStateValue
    candidate_value: int | None
    candidate_id: str | None
    reading_mode: Literal["opencv", "vlm", "hybrid"]
    reading_id: str | None = Field(description="Set while the vision model is reading")
    recognition: RecognitionInfo | None = Field(
        description="OpenCV candidate and the model's answer for the current throw"
    )
    confidence: RecognitionConfidence | None
    confirmation: RecognitionConfirmation | None
    last_confirmation: RecognitionConfirmation | None
    error_code: str | None


class RecognitionCamera(ResponseModel):
    id: int
    label: str


class RecognitionCamerasResponse(ResponseModel):
    cameras: list[RecognitionCamera]


class VisionRoi(ResponseModel):
    x: float
    y: float
    width: float
    height: float


class VisionResolution(ResponseModel):
    width: int
    height: int


class VisionSettingsModel(ResponseModel):
    camera_id: int | None
    roi: VisionRoi
    white_threshold: int | None = Field(description="null: automatic (Otsu)")
    pip_threshold: int | None = Field(description="null: automatic per face")
    camera_resolution: VisionResolution
    exposure: float | None = Field(description="null: automatic exposure")
    automatic_threshold: float
    confirmation_threshold: float
    stationary_frames: int
    consecutive_value_frames: int
    min_pip_size_consistency: float
    max_single_pip_center_offset: float
    max_pip_layout_error: float
    min_detector_confidence: float
    tray_detection: bool = Field(description="Find the tray inside the ROI and use its outline")


class VisionSettingsResponse(ResponseModel):
    settings: VisionSettingsModel = Field(description="Values the recognition uses now")
    saved: VisionSettingsModel | None = Field(
        description="Values in the settings file; null when it cannot be read"
    )
    defaults: VisionSettingsModel
    unsaved: bool
    pending_recognition_settings: bool = Field(
        description="Decision thresholds wait for the current throw to finish"
    )
    settings_path: str | None


class VlmSettingsResponse(ResponseModel):
    reading_mode: Literal["opencv", "vlm", "hybrid"]
    model: str
    timeout_seconds: float
    max_retries: int
    save_images: bool = Field(description="Save sent images under recordings/vlm/")
    installed: bool = Field(description="The anthropic package is installed")
    credentials_configured: bool = Field(description="An API key is set (value never shown)")
    reader_configured: bool
    active_reading_mode: Literal["opencv", "vlm", "hybrid"] | None = Field(
        description="Mode the recognition uses now; changes wait for the current throw"
    )
    pending_reading_mode: bool
    prompt_version: str


class VlmReadingResult(ResponseModel):
    dice_count: int
    value: int | None
    confidence: Literal["high", "medium", "low"]
    note: str


class VlmReadingRecord(ResponseModel):
    reading_id: str | None
    at: str
    mode: str
    status: Literal["used", "discarded", "failed", "test"]
    model: str
    prompt_version: str
    attempts: int
    result: VlmReadingResult | None
    error: str | None
    duration_ms: int | None


class VlmReadingList(ResponseModel):
    readings: list[VlmReadingRecord]


class RoiBrightness(ResponseModel):
    mean: float
    p99: float


class WhiteThresholdInfo(ResponseModel):
    mode: Literal["auto", "fixed"]
    applied: float | None


class DiagnosticDetection(ResponseModel):
    value: int
    confidence: float
    pip_size_consistency: float
    pip_layout_error: float


class DiagnosticRejection(ResponseModel):
    reason: str


class TrayInfo(ResponseModel):
    enabled: bool
    found: bool = Field(description="An outline is in use")
    last_attempt_found: bool | None
    vertices: int | None
    area_ratio: float | None = Field(description="Share of the search area")
    seconds_since_update: float | None


class RecognitionDiagnostics(ResponseModel):
    running: bool
    frame_width: int | None
    frame_height: int | None
    fps: float | None
    motion_score: float | None
    roi_brightness: RoiBrightness | None
    white_threshold: WhiteThresholdInfo | None
    white_area_ratio: float | None
    detections: list[DiagnosticDetection]
    rejected: list[DiagnosticRejection]
    pending_recognition_settings: bool
    tray: TrayInfo
    hints: list[str]


ComponentStatusValue = Literal["ok", "warning", "error", "off", "not_configured"]


class ComponentStatus(ResponseModel):
    status: ComponentStatusValue
    label: str
    detail: str | None


class LoggingStatus(ResponseModel):
    retention: Literal["rotate", "permanent"]
    directory: str
    retention_days: int
    locked: bool
    editable: bool


class RecentProblem(ResponseModel):
    at: str
    level: str
    logger: str
    message: str
    event: str | None
    error_code: str | None


class SystemStatus(ResponseModel):
    """Health of the server and its dependencies for the admin status panel."""

    checked_at: str
    database: ComponentStatus
    camera: ComponentStatus
    commentary: ComponentStatus
    tts: ComponentStatus
    recording: ComponentStatus
    realtime_clients: int
    logging: LoggingStatus
    recent_problems: list[RecentProblem]


class CommentaryResponse(ResponseModel):
    """Commentary for the newest turn of the current game.

    ``generating`` and ``ready`` always refer to ``turnId`` and ``requestId``; a
    comment is replaced or cleared when it is no longer current (undo, a newer roll,
    regeneration, another game, an aborted or saved game). ``source`` is
    ``generated`` for provider output and ``fallback`` when the template comment
    replaced a failed or unusable response (``fallbackReason`` says why).
    """

    status: Literal["idle", "generating", "ready", "failed"]
    game_id: str | None
    turn_id: str | None
    turn_number: int | None
    request_id: str | None
    revision: int | None
    text: str | None
    source: Literal["generated", "fallback"] | None
    fallback_reason: str | None
    provider: str | None
    model: str | None
    duration_ms: float | None
    generated_at: str | None


class SpeechResponse(ResponseModel):
    """What the server is reading aloud right now."""

    # "preparing": the voice is being made and no sound has played yet.
    status: Literal["idle", "preparing", "speaking", "error"]
    kind: Literal["commentary", "preview"] | None
    request_id: str | None
    turn_id: str | None
    text: str | None
    error: str | None
    # The newest comment the player screen may show: its sound has started,
    # speech failed, or speech is off (the text appears together with the voice).
    revealed_request_id: str | None


class ProviderAvailability(ResponseModel):
    installed: bool
    credentials_configured: bool


class CommentarySettingsResponse(ResponseModel):
    enabled: bool
    provider: Literal["fake", "claude", "openai"]
    claude_model: str
    openai_model: str
    language: Literal["ja", "en"]
    length: Literal["short", "medium", "long"]
    style: Literal["lively", "calm", "kids"]
    use_names: bool
    timeout_seconds: float
    max_retries: int
    providers: dict[str, ProviderAvailability]


class CommentaryRequestError(ResponseModel):
    code: str
    message: str


class CommentaryRequestRecord(ResponseModel):
    """One stored generation request (prompt, full response, timing, errors)."""

    id: str
    game_id: str
    turn_id: str
    state_version: int
    revision: int
    trigger: Literal["turn", "regenerate"]
    provider: str
    model: str | None
    prompt_version: str
    system_prompt: str
    user_prompt: str
    status: Literal["pending", "displayed", "discarded", "superseded"]
    outcome: Literal["generated", "fallback", "error"] | None
    response_text: str | None
    final_text: str | None
    fallback_reason: str | None
    error_code: str | None
    error_message: str | None
    attempts: int
    errors: list[CommentaryRequestError]
    provider_request_ids: list[str]
    started_at: str
    finished_at: str | None
    duration_ms: float | None


class CommentaryPromptPreview(ResponseModel):
    """The prompt the next comment would be made with, under the current settings."""

    prompt_version: str
    provider: str
    model: str | None
    system: str
    # Built from the newest turn of the current game; None before the first roll.
    user: str | None
    turn_number: int | None


class CommentaryRequestList(ResponseModel):
    requests: list[CommentaryRequestRecord]


class SpeechProviders(ResponseModel):
    macos: bool = Field(description="macOS voices can be used on this computer")
    google: bool = Field(description="Google Cloud TTS can be used (API key set)")
    google_key_configured: bool = Field(description="GOOGLE_TTS_API_KEY is set (never shown)")
    gemini: bool = Field(description="Gemini TTS can be used (API key set)")
    gemini_key_configured: bool = Field(description="GEMINI_API_KEY is set (never shown)")
    gemini_cloud_configured: bool = Field(
        description="A service account key file is set (GOOGLE_APPLICATION_CREDENTIALS)"
    )


class GeminiVoice(ResponseModel):
    name: str
    gender: str


class SpeechSettingsResponse(ResponseModel):
    enabled: bool
    voice: str | None = Field(description="macOS voice; null: the system default")
    rate: int | None = Field(description="macOS speaking rate (words per minute)")
    volume: float
    provider: Literal["macos", "google", "gemini"]
    google_voice: str
    google_speaking_rate: float
    gemini_backend: Literal["cloud", "api"]
    gemini_model: str
    gemini_voice: str
    gemini_style: str = Field(description="Gemini TTS: how the comment should be read")
    gemini_models: dict[str, list[str]] = Field(description="Models per backend")
    gemini_voices: list[GeminiVoice]
    available: bool = Field(description="The selected provider can be used")
    engine: str
    providers: SpeechProviders


class VoiceInfo(ResponseModel):
    name: str
    locale: str
    sample: str


class VoiceList(ResponseModel):
    voices: list[VoiceInfo]


class GameMessage(ResponseModel):
    type: Literal["game"]
    sequence: int
    payload: GameResponse | None


class RecognitionMessage(ResponseModel):
    type: Literal["recognition"]
    sequence: int
    payload: RecognitionResponse


class CommentaryMessage(ResponseModel):
    type: Literal["commentary"]
    sequence: int
    payload: CommentaryResponse


class SpeechMessage(ResponseModel):
    type: Literal["speech"]
    sequence: int
    payload: SpeechResponse


class RealtimeMessage(
    RootModel[
        Annotated[
            GameMessage | RecognitionMessage | CommentaryMessage | SpeechMessage,
            Field(discriminator="type"),
        ]
    ]
):
    """One message on the ``/ws`` stream, selected by ``type``."""


REALTIME_MESSAGE_ADAPTER: TypeAdapter[RealtimeMessage] = TypeAdapter(RealtimeMessage)
