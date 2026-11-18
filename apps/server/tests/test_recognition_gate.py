from __future__ import annotations

from itertools import count

from commentator.domain.game import GameState, PlayerState
from commentator.main import MemoryGameRepository
from commentator.presets import load_board_preset
from commentator.services.game import GameService
from commentator.services.recognition import RecognitionService
from commentator.services.vision.recognition import (
    DiceRecognitionSession,
    DieObservation,
    FrameObservation,
    RecognitionConfig,
)

from .test_recognition_api import FakeCameraProvider, ObservationPipeline

DIE = (DieObservation(4, 1.0, (10, 10)),)


def still_die() -> FrameObservation:
    return FrameObservation(0, 0.0, detections=DIE, roi_diagonal=100)


def empty() -> FrameObservation:
    return FrameObservation(0, 0.0)


def moving() -> FrameObservation:
    return FrameObservation(0, 0.2)


class Table:
    def __init__(self) -> None:
        self.repository = MemoryGameRepository()
        self.games = GameService(self.repository)
        session = DiceRecognitionSession(RecognitionConfig(
            stationary_frames=1, consecutive_value_frames=1, candidate_window_frames=1,
        ))
        ticks = count(1)
        self.recognition = RecognitionService(
            self.games,
            pipeline=ObservationPipeline(session),
            camera_provider=FakeCameraProvider(),
            clock=lambda: float(next(ticks)),
        )
        self.recognition.attach_game()
        self.game = GameState.create(load_board_preset(), [
            PlayerState("a", "A", "red"), PlayerState("b", "B", "blue"),
        ])
        with self.games.lock:
            self.games.save_or_503(self.game, make_current=True)

    def act(self, name: str) -> None:
        with self.games.lock:
            game = self.repository.current()
            assert game is not None
            getattr(game, name)()
            self.games.save_or_503(game)

    def feed(self, *frames: FrameObservation) -> dict[str, object]:
        snapshot: dict[str, object] = {}
        for frame in frames:
            snapshot = self.recognition.process_frame(frame)
        return snapshot

    def throw(self) -> dict[str, object]:
        return self.feed(moving(), empty(), still_die())

    @property
    def turns(self) -> int:
        game = self.repository.current()
        assert game is not None
        return len(game.turns)


def test_before_start_frames_feed_only_the_preview() -> None:
    table = Table()
    snapshot = table.throw()

    assert snapshot["gameReady"] is False
    assert snapshot["state"] == "waiting"
    assert snapshot["candidateValue"] is None
    assert table.turns == 0


def test_die_already_present_at_start_is_ignored_until_removed() -> None:
    table = Table()
    table.feed(still_die())  # die lying in the tray before the game starts
    table.act("start")

    snapshot = table.feed(*(still_die() for _ in range(10)))
    assert snapshot["gameReady"] is True
    assert snapshot["state"] == "removal_wait"
    assert table.turns == 0

    snapshot = table.feed(empty(), empty(), empty())
    assert snapshot["state"] == "waiting"
    snapshot = table.throw()
    assert snapshot["state"] == "removal_wait"
    assert table.turns == 1


def test_start_with_empty_tray_accepts_the_first_throw() -> None:
    table = Table()
    table.act("start")
    snapshot = table.throw()
    assert table.turns == 1
    assert snapshot["lastConfirmation"] is not None


def test_pause_discards_pending_review_and_resume_ignores_present_die() -> None:
    table = Table()
    table.act("start")
    table.feed(empty(), empty(), empty())
    # Low detector confidence -> needs confirmation.
    table.feed(moving(), empty(), FrameObservation(
        0, 0.0, detections=(DieObservation(4, 0.5, (10, 10)),), roi_diagonal=100,
    ))
    assert table.recognition.snapshot()["state"] == "needs_confirmation"

    table.act("pause")
    snapshot = table.feed(still_die())
    assert snapshot["gameReady"] is False
    assert snapshot["state"] == "waiting"
    assert snapshot["candidateId"] is None

    table.act("resume")
    snapshot = table.feed(*(still_die() for _ in range(5)))
    assert snapshot["state"] == "removal_wait"
    assert table.turns == 0


def test_present_die_is_not_read_after_detection_flickers() -> None:
    """A real camera misses the die for a few frames now and then.

    Those misses must not turn the die that was already lying there into a new throw.
    """
    table = Table()
    table.feed(still_die())
    table.act("start")
    table.feed(still_die(), still_die())
    table.feed(empty(), empty(), empty())  # detection drops out, die never moved
    snapshot = table.feed(*(still_die() for _ in range(10)))

    assert table.turns == 0
    assert snapshot["lastConfirmation"] is None


def test_throw_after_flicker_is_still_recognized() -> None:
    table = Table()
    table.feed(still_die())
    table.act("start")
    table.feed(still_die(), empty(), empty(), empty(), still_die())
    table.throw()
    assert table.turns == 1
