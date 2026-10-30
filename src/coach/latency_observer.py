"""Per-turn latency logging.

An observer, so measuring costs the pipeline nothing: frames are only read, and every line is
buffered in memory and written once at the end of the session.

Rows are keyed on **coach replies**, not on speech starts. Keying them on
``VADUserStartedSpeakingFrame`` produced 74 rows for 8 real turns in the first live session: every
pause, blip and backchannel opened a row, 37 of them entirely null, and because rows were opened
before the coach answered, a scripted question's audio could land in the row belonging to the
*next* turn and read as arriving before the user went quiet.

So the user-side timings are held as pending values and a row is only opened when the LLM actually
starts a reply, which is the first moment a turn is known to be real. A question spoken by a
``tts_say`` pre-action produces no LLM text, so it never opens a row.

``user_stopped_ms`` is when the user actually went quiet, not when end of turn fired.
``VADUserStoppedSpeakingFrame`` carries the ``stop_secs`` silence window it needed to make the
call, which is subtracted back out. ``turn_end_ms`` is when end of turn was decided, so the
turn-detection wait and the LLM wait can be read separately.
"""

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from loguru import logger
from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    EndFrame,
    InterruptionFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    UserStoppedSpeakingFrame,
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


@dataclass
class Pending:
    """The user-side timings seen since the last row was opened."""

    user_stopped_ms: int | None = None
    turn_end_ms: int | None = None
    stt_final_ms: int | None = None

    def clear(self) -> None:
        """Forget the current turn's user-side timings."""
        self.user_stopped_ms = None
        self.turn_end_ms = None
        self.stt_final_ms = None


class LatencyObserver(BaseObserver):
    """Time each coach reply from the user going quiet to the first audio frame."""

    def __init__(self, session_dir: Path, **kwargs) -> None:
        """Initialize the observer against the directory for this session."""
        super().__init__(observe_every_push=False, **kwargs)
        self._dir = session_dir
        self._turns: list[Turn] = []
        self._pending = Pending()
        self._open: Turn | None = None
        self._written = False

    @property
    def turns(self) -> list[Turn]:
        """The turns recorded so far, including any row still open."""
        return self._turns

    async def on_push_frame(self, data: FramePushed) -> None:
        """Collect user-side timings, then open and close a row per coach reply."""
        frame = data.frame
        at_ms = data.timestamp // 1_000_000

        if isinstance(frame, VADUserStoppedSpeakingFrame):
            # The latest quiet moment wins: a pause mid-answer is superseded by the real one.
            self._pending.user_stopped_ms = at_ms - round(frame.stop_secs * 1000)
        elif isinstance(frame, UserStoppedSpeakingFrame):
            self._pending.turn_end_ms = at_ms
        elif isinstance(frame, TranscriptionFrame):
            if frame.text.strip():
                self._pending.stt_final_ms = at_ms
        elif isinstance(frame, LLMTextFrame):
            self._open_row(at_ms)
        elif isinstance(frame, TTSAudioRawFrame):
            if self._open is not None and self._open.tts_first_audio_ms is None:
                self._open.tts_first_audio_ms = at_ms
        elif isinstance(frame, InterruptionFrame):
            if self._open is not None:
                self._open.interrupted = True
                self._close_row()
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._close_row()
        elif isinstance(frame, EndFrame):
            self._close_row()
            self.write()

    def _open_row(self, at_ms: int) -> None:
        """Open a row on the first token of a reply, filling it from the pending timings."""
        if self._open is not None:
            return
        self._open = Turn(
            turn=len(self._turns) + 1,
            user_stopped_ms=self._pending.user_stopped_ms,
            turn_end_ms=self._pending.turn_end_ms,
            stt_final_ms=self._pending.stt_final_ms,
            llm_first_token_ms=at_ms,
        )
        self._turns.append(self._open)
        self._pending.clear()

    def _close_row(self) -> None:
        """Finish the open row, so the next reply starts a new one."""
        self._open = None

    def write(self) -> None:
        """Write latency.jsonl, one line per coach reply. Safe to call more than once."""
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
