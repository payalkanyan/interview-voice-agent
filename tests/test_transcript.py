"""Transcript rendering, including the spec's [INTERRUPTED] rule."""

import json

from coach.transcript import (
    CANDIDATE,
    COACH,
    Line,
    TranscriptRecorder,
    format_timestamp,
    join_spoken,
    render_text,
)


def test_timestamps_are_session_relative_and_zero_padded() -> None:
    assert format_timestamp(0) == "00:00.000"
    assert format_timestamp(9_910) == "00:09.910"
    assert format_timestamp(63_004) == "01:03.004"


def test_words_without_their_own_spacing_are_joined_with_spaces() -> None:
    spoken = ""
    for word in ("You", "mentioned", "retries."):
        spoken = join_spoken(spoken, word, False)
    assert spoken == "You mentioned retries."


def test_words_carrying_their_own_spacing_are_joined_as_is() -> None:
    spoken = join_spoken("", "You", True)
    spoken = join_spoken(spoken, " mentioned", True)
    assert spoken == "You mentioned"


def test_only_interrupted_coach_lines_are_tagged() -> None:
    rendered = render_text(
        [
            Line(COACH, "What was the hardest part?", 1_000),
            Line(CANDIDATE, "Idempotency.", 2_500),
            Line(COACH, "You said retries. What", 4_000, interrupted=True),
        ]
    )
    assert rendered.splitlines() == [
        "[00:01.000] coach: What was the hardest part?",
        "[00:02.500] candidate: Idempotency.",
        "[00:04.000] coach: You said retries. What [INTERRUPTED]",
    ]


def test_an_empty_transcript_renders_empty(tmp_path) -> None:
    assert render_text([]) == ""
    # ...and writes no files, rather than empty ones.
    TranscriptRecorder(tmp_path).write()
    assert not (tmp_path / "transcript.txt").exists()


def test_both_files_are_written_with_matching_content(tmp_path) -> None:
    recorder = TranscriptRecorder(tmp_path)
    recorder._lines = [Line(COACH, "Hello.", 500), Line(CANDIDATE, "Hi.", 1_500)]
    recorder.write()

    assert (tmp_path / "transcript.txt").read_text().startswith("[00:00.500] coach: Hello.")
    parsed = json.loads((tmp_path / "transcript.json").read_text())
    assert parsed == [
        {"speaker": "coach", "text": "Hello.", "at_ms": 500, "interrupted": False},
        {"speaker": "candidate", "text": "Hi.", "at_ms": 1_500, "interrupted": False},
    ]


def test_writing_twice_does_not_duplicate_lines(tmp_path) -> None:
    recorder = TranscriptRecorder(tmp_path)
    recorder._lines = [Line(COACH, "Hello.", 500)]
    recorder.write()
    recorder.write()
    assert len(json.loads((tmp_path / "transcript.json").read_text())) == 1
