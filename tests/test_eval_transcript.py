"""The eval's deterministic rule checks, including the cases that must fail."""

from coach import prompts
from eval_transcript import (
    count_follow_ups,
    count_sentences,
    find_feedback,
    question_positions,
    rule_checks,
)


def line(speaker: str, text: str, at_ms: int = 0, interrupted: bool = False) -> dict:
    return {"speaker": speaker, "text": text, "at_ms": at_ms, "interrupted": interrupted}


def good_transcript() -> list[dict]:
    """A session that satisfies every rule in the spec."""
    lines = [line("coach", f"{prompts.GREETING} {prompts.QUESTIONS[0]}")]
    lines.append(line("candidate", "I built a payments service."))
    lines.append(line("coach", "You mentioned payments. What broke?"))
    lines.append(line("candidate", "Double charges on webhook retries."))
    for question in prompts.QUESTIONS[1:]:
        lines.append(line("coach", f"Got it. {question}"))
        lines.append(line("candidate", "Here is my answer about that."))
    lines.append(line("coach", prompts.BRIDGE))
    lines.append(line("coach", "You were clear about double charges. Next time, quantify it."))
    lines.append(line("coach", prompts.GOODBYE))
    return lines


def test_sentences_are_counted_by_terminal_punctuation() -> None:
    assert count_sentences("One sentence.") == 1
    assert count_sentences("One. Two.") == 2
    assert count_sentences("One. Two! Three?") == 3
    assert count_sentences("No terminator") == 1
    assert count_sentences("   ") == 0


def test_every_question_is_located_in_order() -> None:
    positions = question_positions(good_transcript())
    assert all(p is not None for p in positions)
    assert positions == sorted(positions)


def test_a_missing_question_is_reported_as_missing() -> None:
    lines = [ln for ln in good_transcript() if prompts.QUESTIONS[2] not in ln["text"]]
    assert question_positions(lines)[2] is None


def test_the_nudge_is_not_counted_as_a_follow_up() -> None:
    lines = [
        line("coach", prompts.QUESTIONS[0]),
        line("candidate", "um"),
        line("coach", prompts.NUDGE),
        line("candidate", "ok here goes"),
        line("coach", prompts.QUESTIONS[1]),
    ]
    assert count_follow_ups(lines, question_positions(lines))[0] == 0


def test_an_acknowledgement_is_not_counted_as_a_follow_up() -> None:
    lines = [
        line("coach", prompts.QUESTIONS[0]),
        line("candidate", "I built a thing."),
        line("coach", "That sounds solid."),
        line("coach", prompts.QUESTIONS[1]),
    ]
    assert count_follow_ups(lines, question_positions(lines))[0] == 0


def test_two_follow_ups_on_one_question_are_counted() -> None:
    lines = [
        line("coach", prompts.QUESTIONS[0]),
        line("candidate", "A thing."),
        line("coach", "What kind of thing?"),
        line("candidate", "A service."),
        line("coach", "Serving what?"),
        line("coach", prompts.QUESTIONS[1]),
    ]
    assert count_follow_ups(lines, question_positions(lines))[0] == 2


def test_feedback_is_found_between_the_bridge_and_the_goodbye() -> None:
    assert find_feedback(good_transcript()).startswith("You were clear")


def test_feedback_is_absent_when_the_session_ended_early() -> None:
    lines = [ln for ln in good_transcript() if ln["text"] != prompts.BRIDGE]
    # Without the bridge there is nothing marking where feedback starts.
    assert find_feedback(lines) is None


def test_a_good_session_passes_every_rule() -> None:
    results, feedback = rule_checks(good_transcript())
    assert feedback is not None
    assert [description for passed, description in results if not passed] == []


def test_out_of_order_questions_fail_the_order_check() -> None:
    lines = good_transcript()
    swapped = [ln for ln in lines if prompts.QUESTIONS[1] not in ln["text"]]
    swapped.insert(0, line("coach", prompts.QUESTIONS[1]))
    results, _ = rule_checks(swapped)
    assert any("in order" in description for passed, description in results if not passed)


def test_a_one_sentence_feedback_fails_the_length_check() -> None:
    lines = good_transcript()
    for ln in lines:
        if ln["text"].startswith("You were clear"):
            ln["text"] = "You did well."
    results, _ = rule_checks(lines)
    assert any("2 to 3 sentences" in description for passed, description in results if not passed)
