"""Session transcripts.

Written by an observer, so nothing here sits in the pipeline's path. Coach lines are rebuilt
from ``TTSTextFrame``, which Pipecat emits in strict playback order, so a line cut short by an
interruption contains only the words that were actually spoken.
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from loguru import logger
from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    EndFrame,
    InterruptionFrame,
    TranscriptionFrame,
    TTSTextFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed

COACH = "coach"
CANDIDATE = "candidate"


@dataclass
class Line:
    """One spoken turn, timed from the start of the session."""

    speaker: str
    text: str
    at_ms: int
    interrupted: bool = field(default=False)


def format_timestamp(at_ms: int) -> str:
    """Render a session-relative millisecond offset as mm:ss.mmm."""
    minutes, remainder = divmod(at_ms, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{minutes:02d}:{seconds:02d}.{millis:03d}"


def render_text(lines: list[Line]) -> str:
    """Render the transcript as the plain text form written to transcript.txt."""
    out = []
    for line in lines:
        suffix = " [INTERRUPTED]" if line.interrupted else ""
        out.append(f"[{format_timestamp(line.at_ms)}] {line.speaker}: {line.text}{suffix}")
    return "\n".join(out) + "\n" if out else ""


def join_spoken(spoken: str, text: str, includes_spaces: bool) -> str:
    """Append one TTS word to the words spoken so far, respecting the frame's own spacing."""
    if not spoken:
        return text.strip() if not includes_spaces else text
    if includes_spaces:
        return spoken + text
    return f"{spoken} {text}"


class TranscriptRecorder(BaseObserver):
    """Record who said what, in order, and mark coach lines that were cut short."""

    def __init__(self, session_dir: Path, **kwargs) -> None:
        """Initialize the recorder against the directory for this session."""
        super().__init__(observe_every_push=False, **kwargs)
        self._dir = session_dir
        self._lines: list[Line] = []
        self._spoken = ""
        self._spoken_at_ms = 0
        self._candidate = ""
        self._candidate_at_ms = 0
        self._written = False

    @property
    def lines(self) -> list[Line]:
        """The transcript recorded so far."""
        return self._lines

    async def on_push_frame(self, data: FramePushed) -> None:
        """Build transcript lines from transcription and TTS playback frames."""
        frame = data.frame
        at_ms = data.timestamp // 1_000_000

        if isinstance(frame, TranscriptionFrame):
            if frame.text.strip():
                if not self._candidate:
                    self._candidate_at_ms = at_ms
                self._candidate = f"{self._candidate} {frame.text.strip()}".strip()
        elif isinstance(frame, TTSTextFrame):
            self._flush_candidate()
            if not self._spoken:
                self._spoken_at_ms = at_ms
            self._spoken = join_spoken(
                self._spoken, frame.text, bool(frame.includes_inter_frame_spaces)
            )
        elif isinstance(frame, InterruptionFrame):
            self._flush_coach(interrupted=True)
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._flush_coach(interrupted=False)
        elif isinstance(frame, EndFrame):
            self.write()

    def _flush_candidate(self) -> None:
        """Close off the candidate's turn as one line."""
        if self._candidate:
            self._lines.append(Line(CANDIDATE, self._candidate, self._candidate_at_ms))
            self._candidate = ""

    def _flush_coach(self, *, interrupted: bool) -> None:
        """Close off the coach's line, keeping only the words actually spoken."""
        if self._spoken:
            self._lines.append(
                Line(COACH, self._spoken.strip(), self._spoken_at_ms, interrupted=interrupted)
            )
            self._spoken = ""

    def write(self) -> None:
        """Write transcript.txt and transcript.json. Safe to call more than once."""
        if self._written:
            return
        self._flush_coach(interrupted=False)
        self._flush_candidate()
        if not self._lines:
            return

        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / "transcript.txt").write_text(render_text(self._lines))
        (self._dir / "transcript.json").write_text(
            json.dumps([asdict(line) for line in self._lines], indent=2) + "\n"
        )
        self._written = True
        logger.info(f"Wrote transcript ({len(self._lines)} lines) to {self._dir}")

    async def cleanup(self) -> None:
        """Flush the transcript if the session ended without an EndFrame."""
        self.write()
        await super().cleanup()
