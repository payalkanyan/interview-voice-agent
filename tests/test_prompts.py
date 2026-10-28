"""Prompt invariants, and the promise that PROMPTS.md quotes the prompts as they are used."""

from pathlib import Path

import pytest

from coach import prompts

DOC = Path(__file__).resolve().parents[1] / "PROMPTS.md"

FIXED_LINES = (
    "PERSONA",
    "GREETING",
    "NUDGE",
    "BRIDGE",
    "GOODBYE",
    "FOLLOW_UP_SPENT",
    "FOLLOW_UP_RESET",
    "FEEDBACK_SYSTEM",
)


@pytest.fixture(scope="module")
def doc() -> str:
    return DOC.read_text()


@pytest.mark.parametrize("name", FIXED_LINES)
def test_prompts_md_quotes_each_prompt_verbatim(doc: str, name: str) -> None:
    assert getattr(prompts, name).strip() in doc


def test_prompts_md_quotes_all_four_questions(doc: str) -> None:
    for question in prompts.QUESTIONS:
        assert question in doc


def test_prompts_md_quotes_the_reaction_task(doc: str) -> None:
    # The doc shows the template, so substitute the placeholders back in.
    question = prompts.QUESTIONS[0]
    rendered = (
        prompts.reaction_task(question)
        .replace(f'"{question}"', '"{question}"')
        .replace(f'"{prompts.NUDGE}"', '"{nudge}"')
    )
    assert rendered in doc


def test_the_four_questions_are_exactly_the_spec_wording() -> None:
    assert prompts.QUESTIONS == (
        "Tell me about a project you're proud of.",
        "What was the hardest technical problem in it?",
        "How did you know it was working well?",
        "What would you do differently now?",
    )


def test_the_reaction_task_names_all_three_branches_and_the_only_tool() -> None:
    task = prompts.reaction_task(prompts.QUESTIONS[0])
    for branch in ("MOVE ON", "FOLLOW UP", "NUDGE"):
        assert branch in task
    assert task.count("next_question") >= 3
    assert prompts.NUDGE in task


def test_spoken_prompts_forbid_characters_a_tts_engine_would_read_aloud() -> None:
    for text in (prompts.PERSONA, prompts.FEEDBACK_SYSTEM):
        assert "no markdown" in text
        assert "no emoji" in text


def test_fixed_spoken_lines_contain_no_markdown_characters() -> None:
    spoken = (prompts.GREETING, prompts.NUDGE, prompts.BRIDGE, prompts.GOODBYE, *prompts.QUESTIONS)
    for line in spoken:
        assert not set(line) & set("*_#`[]<>|")


def test_the_fallback_feedback_line_is_two_sentences() -> None:
    # It stands in for real feedback, so it must still satisfy the spec's 2-to-3 sentence rule.
    assert prompts.FEEDBACK_FALLBACK.count(".") == 2
