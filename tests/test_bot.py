"""The pipeline assembles, and every tunable in the README really comes from the environment.

These checks run against the real Pipecat classes with dummy keys. No network is touched: the
services only connect when a session starts. The point is to catch a miswired pipeline before a
recording session rather than during one.
"""

import pytest
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.turns.user_start.min_words_user_turn_start_strategy import (
    MinWordsUserTurnStartStrategy,
)
from pipecat.turns.user_stop.turn_analyzer_user_turn_stop_strategy import (
    TurnAnalyzerUserTurnStopStrategy,
)

from coach import bot

KEYS = {
    "DEEPGRAM_API_KEY": "test",
    "CARTESIA_API_KEY": "test",
    "CARTESIA_VOICE_ID": "test-voice",
    "GOOGLE_API_KEY": "test",
}


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    for name, value in KEYS.items():
        monkeypatch.setenv(name, value)


def test_a_missing_key_fails_with_a_useful_message(monkeypatch):
    monkeypatch.delenv("DEEPGRAM_API_KEY")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DEEPGRAM_API_KEY is not set"):
        bot.build_stt()


def test_the_gemini_key_may_be_named_either_way(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY")
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    assert bot.build_llm() is not None


def test_models_default_to_the_values_documented_in_design(monkeypatch):
    for name in ("DEEPGRAM_MODEL", "CARTESIA_MODEL", "GEMINI_CONVERSATION_MODEL"):
        monkeypatch.delenv(name, raising=False)
    assert bot.build_stt()._settings.model == "nova-3-general"
    assert bot.build_llm()._settings.model == "gemini-3.1-flash-lite"
    assert bot.build_tts()._settings.model == "sonic-3.6"


def test_models_can_be_overridden_from_the_environment(monkeypatch):
    monkeypatch.setenv("GEMINI_CONVERSATION_MODEL", "gemini-3.5-flash-lite")
    assert bot.build_llm()._settings.model == "gemini-3.5-flash-lite"


def test_stt_keyterms_are_parsed_from_a_comma_separated_list(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_KEYTERMS", "idempotency, Kubernetes ,  ")
    assert bot.build_stt()._settings.keyterm == ["idempotency", "Kubernetes"]


def test_no_keyterms_leaves_the_setting_unset(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_KEYTERMS", "")
    assert bot.build_stt()._settings.keyterm is None


def test_vad_thresholds_default_to_pipecats_own_defaults(monkeypatch):
    for name in ("VAD_CONFIDENCE", "VAD_START_SECS", "VAD_STOP_SECS", "VAD_MIN_VOLUME"):
        monkeypatch.delenv(name, raising=False)
    params = bot.build_vad()._params
    assert (params.confidence, params.start_secs, params.stop_secs, params.min_volume) == (
        0.7,
        0.2,
        0.2,
        0.6,
    )


def test_vad_thresholds_are_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("VAD_STOP_SECS", "0.45")
    assert bot.build_vad()._params.stop_secs == 0.45


def test_turn_detection_is_smart_turn_v3_with_a_word_floor_on_interruptions(monkeypatch):
    monkeypatch.delenv("INTERRUPT_MIN_WORDS", raising=False)
    strategies = bot.build_turn_strategies()

    assert len(strategies.start) == 1
    start = strategies.start[0]
    assert isinstance(start, MinWordsUserTurnStartStrategy)
    # The spec asks for at least two words, so a cough or "mm hmm" cannot interrupt.
    assert start._min_words == 2

    assert len(strategies.stop) == 1
    stop = strategies.stop[0]
    assert isinstance(stop, TurnAnalyzerUserTurnStopStrategy)
    assert isinstance(stop._turn_analyzer, LocalSmartTurnAnalyzerV3)


def test_the_interruption_word_floor_is_tunable(monkeypatch):
    monkeypatch.setenv("INTERRUPT_MIN_WORDS", "4")
    assert bot.build_turn_strategies().start[0]._min_words == 4


def test_each_session_gets_its_own_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    created = bot.make_session_dir()
    assert created.exists()
    assert created.name.startswith("session-")
    assert created.parent == tmp_path
