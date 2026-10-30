"""The latency observer: one row per coach reply, and nothing for scripted questions.

The first live session produced 74 rows for roughly 8 real turns, 37 of them entirely null, and
3 where the first audio appeared to arrive before the user went quiet. These tests drive the
observer with synthetic frames shaped like that session.
"""

import json

from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    EndFrame,
    InterruptionFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import FramePushed

from coach.latency_observer import LatencyObserver

VAD_STOP_SECS = 0.2


def audio() -> TTSAudioRawFrame:
    return TTSAudioRawFrame(audio=b"\x00\x00", sample_rate=16000, num_channels=1)


def transcript(text: str) -> TranscriptionFrame:
    return TranscriptionFrame(text=text, user_id="u", timestamp="t")


class Driver:
    """Pushes frames at chosen session times."""

    def __init__(self, tmp_path) -> None:
        self.observer = LatencyObserver(tmp_path)
        self.dir = tmp_path

    async def push(self, frame, at_ms: int) -> None:
        await self.observer.on_push_frame(
            FramePushed(
                source=None,
                destination=None,
                frame=frame,
                direction=None,
                timestamp=at_ms * 1_000_000,
            )
        )

    async def scripted_question(self, at_ms: int) -> None:
        """A question spoken by a tts_say pre-action: audio, but no LLM text."""
        await self.push(audio(), at_ms)
        await self.push(BotStoppedSpeakingFrame(), at_ms + 2_000)

    async def answered_turn(self, start_ms: int, *, interrupted: bool = False) -> None:
        """A complete turn: the user speaks, goes quiet, and the coach replies."""
        await self.push(VADUserStartedSpeakingFrame(start_secs=0.2), start_ms)
        await self.push(transcript("an answer"), start_ms + 3_000)
        await self.push(VADUserStoppedSpeakingFrame(stop_secs=VAD_STOP_SECS), start_ms + 4_200)
        await self.push(UserStoppedSpeakingFrame(), start_ms + 4_500)
        await self.push(LLMTextFrame(text="Nice"), start_ms + 4_800)
        await self.push(audio(), start_ms + 4_950)
        if interrupted:
            await self.push(InterruptionFrame(), start_ms + 5_400)
        else:
            await self.push(BotStoppedSpeakingFrame(), start_ms + 6_000)


async def test_the_opening_question_opens_no_row(tmp_path) -> None:
    driver = Driver(tmp_path)
    await driver.scripted_question(500)
    assert driver.observer.turns == []


async def test_pauses_and_blips_open_no_rows(tmp_path) -> None:
    # This is what produced 37 null rows: speech starts with no reply behind them.
    driver = Driver(tmp_path)
    for start in (5_000, 6_000, 7_000):
        await driver.push(VADUserStartedSpeakingFrame(start_secs=0.2), start)
        await driver.push(VADUserStoppedSpeakingFrame(stop_secs=VAD_STOP_SECS), start + 400)
    assert driver.observer.turns == []


async def test_one_answered_turn_makes_exactly_one_row(tmp_path) -> None:
    driver = Driver(tmp_path)
    await driver.scripted_question(500)
    await driver.answered_turn(5_000)
    assert len(driver.observer.turns) == 1

    turn = driver.observer.turns[0]
    assert turn.turn == 1
    # 4200 ms in, minus the 200 ms of silence VAD needed to call it.
    assert turn.user_stopped_ms == 9_000
    assert turn.turn_end_ms == 9_500
    assert turn.stt_final_ms == 8_000
    assert turn.llm_first_token_ms == 9_800
    assert turn.tts_first_audio_ms == 9_950
    assert turn.interrupted is False


async def test_eight_answered_turns_make_eight_rows(tmp_path) -> None:
    driver = Driver(tmp_path)
    await driver.scripted_question(500)
    for index in range(8):
        await driver.answered_turn(10_000 + index * 20_000)
        await driver.scripted_question(10_000 + index * 20_000 + 8_000)
    assert len(driver.observer.turns) == 8
    assert [t.turn for t in driver.observer.turns] == list(range(1, 9))


async def test_every_row_has_both_endpoints_in_the_right_order(tmp_path) -> None:
    driver = Driver(tmp_path)
    for index in range(4):
        await driver.answered_turn(5_000 + index * 20_000, interrupted=index == 2)
    for turn in driver.observer.turns:
        assert turn.user_stopped_ms is not None
        assert turn.tts_first_audio_ms is not None
        # The bug that made the first session's numbers meaningless.
        assert turn.tts_first_audio_ms > turn.user_stopped_ms
        assert turn.user_stopped_ms <= turn.turn_end_ms <= turn.llm_first_token_ms


async def test_an_interruption_marks_and_closes_the_row(tmp_path) -> None:
    driver = Driver(tmp_path)
    await driver.answered_turn(5_000, interrupted=True)
    await driver.answered_turn(20_000)
    assert [t.interrupted for t in driver.observer.turns] == [True, False]


async def test_a_second_reply_without_new_speech_opens_its_own_row(tmp_path) -> None:
    # A follow-up reply after the coach was cut off still gets its own row, with no user-side
    # timings, rather than being folded into the previous one.
    driver = Driver(tmp_path)
    await driver.answered_turn(5_000, interrupted=True)
    await driver.push(LLMTextFrame(text="As I was saying"), 11_000)
    await driver.push(audio(), 11_200)
    await driver.push(BotStoppedSpeakingFrame(), 12_000)

    assert len(driver.observer.turns) == 2
    second = driver.observer.turns[1]
    assert second.llm_first_token_ms == 11_000
    assert second.user_stopped_ms is None


async def test_the_log_is_written_once_with_one_line_per_row(tmp_path) -> None:
    driver = Driver(tmp_path)
    await driver.answered_turn(5_000)
    await driver.answered_turn(20_000)
    await driver.push(EndFrame(), 40_000)

    lines = (tmp_path / "latency.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert set(first) == {
        "turn",
        "user_stopped_ms",
        "turn_end_ms",
        "stt_final_ms",
        "llm_first_token_ms",
        "tts_first_audio_ms",
        "interrupted",
    }

    driver.observer.write()
    assert len((tmp_path / "latency.jsonl").read_text().strip().splitlines()) == 2


async def test_no_turns_writes_no_file(tmp_path) -> None:
    driver = Driver(tmp_path)
    await driver.scripted_question(500)
    await driver.push(EndFrame(), 5_000)
    assert not (tmp_path / "latency.jsonl").exists()
