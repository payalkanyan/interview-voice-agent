"""Per-turn latency logging.

An observer, so measuring costs the pipeline nothing: frames are only read, and every line is
buffered in memory and written once at the end of the session.

``user_stopped_ms`` is when the user actually went quiet, not when end of turn fired.
Pipecat reports those as two different frames, and ``VADUserStoppedSpeakingFrame`` carries the
``stop_secs`` silence window it needed to make the call, which is subtracted back out.
``turn_end_ms`` is when end of turn was decided, so the turn-detection wait and the LLM wait can
be read separately.
"""

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from loguru import logger
from pipecat.frames.frames import (
    EndFrame,
    InterruptionFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed


@dataclass
class Turn:
    """One user turn, with every stage timed in milliseconds from session start."""

    turn: int
    user_stopped_ms: int | None = None
    turn_end_ms: int | None = None
    stt_final_ms: int | None = None
    llm_first_token_ms: int | None = None
    tts_first_audio_ms: int | None = None
    interrupted: bool = False


class LatencyObserver(BaseObserver):
    """Time each user turn from going quiet to the coach's first audio."""

    def __init__(self, session_dir: Path, **kwargs) -> None:
        """Initialize the observer against the directory for this session."""
        super().__init__(observe_every_push=False, **kwargs)
        self._dir = session_dir
        self._turns: list[Turn] = []
        self._current: Turn | None = None
        self._written = False

    @property
    def turns(self) -> list[Turn]:
        """The turns recorded so far, including the one in progress."""
        return self._turns

    async def on_push_frame(self, data: FramePushed) -> None:
        """Record the first time each stage happens in the current turn."""
        frame = data.frame
        at_ms = data.timestamp // 1_000_000

        if isinstance(frame, VADUserStartedSpeakingFrame):
            self._current = Turn(turn=len(self._turns) + 1)
            self._turns.append(self._current)
            return

        if isinstance(frame, EndFrame):
            self.write()
            return

        turn = self._current
        if turn is None:
            # The greeting is spoken before anyone has taken a turn.
            return

        if isinstance(frame, VADUserStoppedSpeakingFrame):
            if turn.user_stopped_ms is None:
                turn.user_stopped_ms = at_ms - round(frame.stop_secs * 1000)
        elif isinstance(frame, UserStoppedSpeakingFrame):
            if turn.turn_end_ms is None:
                turn.turn_end_ms = at_ms
        elif isinstance(frame, TranscriptionFrame):
            if turn.stt_final_ms is None:
                turn.stt_final_ms = at_ms
        elif isinstance(frame, LLMTextFrame):
            if turn.llm_first_token_ms is None:
                turn.llm_first_token_ms = at_ms
        elif isinstance(frame, TTSAudioRawFrame):
            if turn.tts_first_audio_ms is None:
                turn.tts_first_audio_ms = at_ms
        elif isinstance(frame, InterruptionFrame):
            turn.interrupted = True

    def write(self) -> None:
        """Write latency.jsonl, one line per user turn. Safe to call more than once."""
        if self._written or not self._turns:
            return
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / "latency.jsonl"
        with path.open("w") as handle:
            for turn in self._turns:
                handle.write(json.dumps(asdict(turn)) + "\n")
        self._written = True
        logger.info(f"Wrote {len(self._turns)} turns of latency data to {path}")

    async def cleanup(self) -> None:
        """Flush the log if the session ended without an EndFrame."""
        self.write()
        await super().cleanup()
