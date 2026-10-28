"""The latency report's arithmetic and its handling of incomplete turns."""

import json

import pytest

from latency_report import load_turns, percentile, report, stage_averages, total_latencies

TURNS = [
    {
        "turn": 1,
        "user_stopped_ms": 9_000,
        "turn_end_ms": 9_450,
        "stt_final_ms": 9_500,
        "llm_first_token_ms": 9_780,
        "tts_first_audio_ms": 9_910,
        "interrupted": False,
    },
    {
        "turn": 2,
        "user_stopped_ms": 14_000,
        "turn_end_ms": 14_500,
        "stt_final_ms": 14_560,
        "llm_first_token_ms": 14_900,
        "tts_first_audio_ms": 15_050,
        "interrupted": True,
    },
]


def test_percentile_of_one_sample_is_that_sample() -> None:
    assert percentile([910], 0.95) == 910


def test_percentile_interpolates_between_samples() -> None:
    assert percentile([0, 100], 0.5) == 50
    assert percentile([0, 10, 20, 30, 40], 0.5) == 20


def test_percentile_ignores_input_order() -> None:
    assert percentile([30, 10, 20], 0.5) == percentile([10, 20, 30], 0.5)


def test_percentile_of_nothing_is_an_error() -> None:
    with pytest.raises(ValueError):
        percentile([], 0.5)


def test_total_latency_is_quiet_to_first_audio() -> None:
    assert total_latencies(TURNS) == [910, 1_050]


def test_turns_missing_an_endpoint_are_left_out_of_totals() -> None:
    incomplete = [{"turn": 3, "user_stopped_ms": 1_000, "tts_first_audio_ms": None}]
    assert total_latencies(TURNS + incomplete) == [910, 1_050]


def test_stage_averages_cover_every_stage_and_count_their_samples() -> None:
    averages = dict((label, value) for label, value, _ in stage_averages(TURNS))
    assert averages["turn detection"] == pytest.approx(475)
    assert averages["stt final"] == pytest.approx(55)
    assert averages["llm first token"] == pytest.approx(310)
    assert averages["tts first audio"] == pytest.approx(140)
    assert all(count == 2 for _, _, count in stage_averages(TURNS))


def test_a_stage_with_no_samples_is_omitted_rather_than_shown_as_zero() -> None:
    partial = [{"turn": 1, "user_stopped_ms": 1_000, "turn_end_ms": 1_400}]
    assert [label for label, _, _ in stage_averages(partial)] == ["turn detection"]


def test_missing_log_is_a_clear_error(tmp_path) -> None:
    with pytest.raises(SystemExit):
        load_turns(tmp_path)


def test_empty_log_is_a_clear_error(tmp_path) -> None:
    (tmp_path / "latency.jsonl").write_text("\n")
    with pytest.raises(SystemExit):
        load_turns(tmp_path)


def test_report_names_the_turn_count_interruptions_and_the_playback_caveat(tmp_path) -> None:
    (tmp_path / "latency.jsonl").write_text("\n".join(json.dumps(turn) for turn in TURNS) + "\n")
    text = report(tmp_path)
    assert "Turns: 2 (1 interrupted)" in text
    assert "p50" in text and "p95" in text
    assert "server send time" in text
