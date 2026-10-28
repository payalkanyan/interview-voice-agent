"""The interview flow's guarantees: question order, verbatim wording, one follow-up each."""

import types

import pytest
from pipecat.processors.aggregators.llm_context import LLMContext

from coach import flow, prompts


def walk_flow() -> list[dict]:
    """Walk the flow from the start, moving on at every question, and return the nodes visited."""
    manager = types.SimpleNamespace(state={"question_index": 0})
    nodes = [flow.initial_node()]
    for _ in range(flow.QUESTION_COUNT):
        _, node = pytest.importorskip("asyncio").run(flow.next_question(manager))
        nodes.append(node)
    return nodes


def test_all_four_questions_are_asked_in_spec_order() -> None:
    spoken = [node["pre_actions"][0]["text"] for node in walk_flow()]
    for question, said in zip(prompts.QUESTIONS, spoken, strict=False):
        assert question in said


def test_questions_are_spoken_word_for_word() -> None:
    # The questions are spoken by a tts_say action, not generated, so each is exact.
    nodes = walk_flow()
    assert nodes[0]["pre_actions"][0]["text"].endswith(prompts.QUESTIONS[0])
    for index in range(1, flow.QUESTION_COUNT):
        assert nodes[index]["pre_actions"][0]["text"] == prompts.QUESTIONS[index]


def test_greeting_precedes_the_first_question_only() -> None:
    nodes = walk_flow()
    assert nodes[0]["pre_actions"][0]["text"].startswith(prompts.GREETING)
    for node in nodes[1:]:
        assert prompts.GREETING not in node["pre_actions"][0]["text"]


def test_persona_is_set_once_on_the_first_node() -> None:
    nodes = walk_flow()
    assert nodes[0]["role_message"] == prompts.PERSONA
    # role_message persists across transitions in Pipecat Flows, so later nodes must not reset it.
    for node in nodes[1:]:
        assert "role_message" not in node


def test_the_interview_ends_in_feedback_then_goodbye() -> None:
    last = walk_flow()[-1]
    assert last["name"] == "feedback"
    actions = [action["type"] for action in last["pre_actions"]]
    assert actions == ["tts_say", "function", "end_conversation"]
    assert last["pre_actions"][0]["text"] == prompts.BRIDGE
    assert last["pre_actions"][-1]["text"] == prompts.GOODBYE


def test_nodes_wait_for_the_answer_instead_of_talking_over_it() -> None:
    for node in walk_flow():
        assert node["respond_immediately"] is False


def test_every_question_node_can_only_move_on() -> None:
    # next_question is the single tool, so follow-up and nudge cost no extra round trip.
    for node in walk_flow()[:-1]:
        assert [f.__name__ for f in node["functions"]] == ["next_question"]


def _limiter() -> tuple[flow.FollowUpLimiter, LLMContext]:
    context = LLMContext()
    return flow.FollowUpLimiter(context), context


def _reply(limiter: flow.FollowUpLimiter, text: str, *, advanced: bool = False) -> None:
    limiter._reply = text
    limiter._advanced = False
    if advanced:
        limiter._begin_advance()
    limiter._spend_follow_up()


def test_a_nudge_does_not_spend_the_follow_up() -> None:
    limiter, context = _limiter()
    _reply(limiter, prompts.NUDGE)
    assert limiter.follow_up_used is False
    assert context.get_messages() == []


def test_a_follow_up_spends_the_budget_and_constrains_the_next_turn() -> None:
    limiter, context = _limiter()
    _reply(limiter, "You mentioned a payments service. What made it hard?")
    assert limiter.follow_up_used is True
    assert context.get_messages()[-1]["content"] == prompts.FOLLOW_UP_SPENT


def test_a_second_follow_up_is_refused_without_repeating_the_constraint() -> None:
    limiter, context = _limiter()
    _reply(limiter, "What made it hard?")
    _reply(limiter, "And the database side?")
    assert len(context.get_messages()) == 1


def test_an_empty_reply_spends_nothing() -> None:
    limiter, _ = _limiter()
    _reply(limiter, "   ")
    assert limiter.follow_up_used is False


def test_advancing_restores_the_budget_for_the_next_question() -> None:
    limiter, context = _limiter()
    _reply(limiter, "What made it hard?")
    _reply(limiter, "Good, thanks.", advanced=True)
    assert limiter.follow_up_used is False
    assert context.get_messages()[-1]["content"] == prompts.FOLLOW_UP_RESET

    # ...and the next question really can have its own follow-up.
    _reply(limiter, "You said tests. Which ones?")
    assert limiter.follow_up_used is True
