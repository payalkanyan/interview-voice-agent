"""The code backstop that guarantees the feedback node is reached.

In the first live session the coach invented a goodbye on question four and never called
next_question, so the flow never left the question node and the session ended with no feedback.
These tests cover the backstop that makes reaching the feedback independent of the model.
"""

import asyncio

import pytest
from pipecat.clocks.system_clock import SystemClock
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameProcessorSetup
from pipecat.utils.asyncio.task_manager import TaskManager

from coach import flow, prompts


class FakeFlowManager:
    """Records transitions and exposes the parts of FlowManager the flow actually uses."""

    def __init__(self, question_index: int, current_node: str) -> None:
        self.state = {"question_index": question_index, "context": LLMContext()}
        self.current_node = current_node
        self.transitions: list[str] = []

    async def set_node_from_config(self, node) -> None:
        self.transitions.append(node["name"])
        self.current_node = node["name"]


def _cancel_feedback(manager: FakeFlowManager) -> None:
    task = manager.state.get("feedback_task")
    if task is not None:
        task.cancel()


async def _setup_limiter(context: LLMContext, on_stalled) -> flow.FollowUpLimiter:
    limiter = flow.FollowUpLimiter(context, on_stalled=on_stalled)
    await limiter.setup(
        FrameProcessorSetup(
            clock=SystemClock(),
            task_manager=TaskManager(loop=asyncio.get_running_loop()),
            pipeline_worker=None,
        )
    )
    return limiter


async def test_a_stall_on_the_last_question_reaches_the_feedback_node() -> None:
    manager = FakeFlowManager(question_index=3, current_node="question_4")
    await flow.force_advance(manager)
    assert manager.transitions == ["feedback"]
    assert manager.current_node == "feedback"
    # Feedback generation starts with the transition, so the bridge line covers the wait.
    assert "feedback_task" in manager.state
    _cancel_feedback(manager)


async def test_a_stall_mid_interview_advances_to_the_next_question() -> None:
    manager = FakeFlowManager(question_index=0, current_node="question_1")
    await flow.force_advance(manager)
    assert manager.transitions == ["question_2"]
    assert manager.state["question_index"] == 1


async def test_the_backstop_does_nothing_once_the_feedback_node_is_live() -> None:
    manager = FakeFlowManager(question_index=4, current_node="feedback")
    await flow.force_advance(manager)
    assert manager.transitions == []


async def test_a_spent_follow_up_without_an_advance_schedules_the_backstop() -> None:
    stalled = asyncio.Event()

    async def on_stalled() -> None:
        stalled.set()

    context = LLMContext()
    limiter = await _setup_limiter(context, on_stalled)
    limiter.set_question_index(3)

    def reply(text: str) -> None:
        limiter._reply = text
        limiter._advanced = False
        limiter._spend_follow_up()

    reply("You said HTTP requests. Which handler?")
    assert limiter.follow_up_used is True
    assert not stalled.is_set()

    # The exact line the live session ended on.
    reply("That sounds like a much more efficient flow. Thanks for sharing those details.")
    await asyncio.wait_for(stalled.wait(), timeout=2)
    await limiter.cleanup()


async def test_a_re_ask_with_the_budget_spent_does_not_trigger_the_backstop() -> None:
    # Re-asking an unanswered question is correct behaviour; advancing would skip the question.
    stalled = asyncio.Event()

    async def on_stalled() -> None:
        stalled.set()

    limiter = await _setup_limiter(LLMContext(), on_stalled)
    limiter.set_question_index(3)

    def reply(text: str) -> None:
        limiter._reply = text
        limiter._advanced = False
        limiter._spend_follow_up()

    reply("You said HTTP requests. Which handler?")
    reply(f"Sorry, let me ask again. {prompts.QUESTIONS[3]}")
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(stalled.wait(), timeout=0.3)
    await limiter.cleanup()


async def test_a_nudge_with_the_budget_spent_does_not_trigger_the_backstop() -> None:
    stalled = asyncio.Event()

    async def on_stalled() -> None:
        stalled.set()

    limiter = await _setup_limiter(LLMContext(), on_stalled)
    limiter.set_question_index(1)

    def reply(text: str) -> None:
        limiter._reply = text
        limiter._advanced = False
        limiter._spend_follow_up()

    reply("What part was hardest?")
    reply(prompts.NUDGE)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(stalled.wait(), timeout=0.3)
    await limiter.cleanup()


async def test_a_missing_task_manager_does_not_crash_the_pipeline() -> None:
    # The limiter is not set up here, so create_task raises. A failed backstop must be survivable.
    async def on_stalled() -> None:
        pass

    limiter = flow.FollowUpLimiter(LLMContext(), on_stalled=on_stalled)
    limiter.set_question_index(0)
    limiter._follow_up_used = True
    limiter._reply = "Thanks, that is everything."
    limiter._advanced = False
    limiter._spend_follow_up()  # must not raise


async def _captured_feedback_model(monkeypatch) -> str:
    """Run the feedback kick-off and report which model it asked for."""
    asked: dict[str, str] = {}

    async def fake_generate(transcript: str, model: str, api_key: str) -> str:
        asked["model"] = model
        return "ok"

    monkeypatch.setattr(flow, "generate_feedback", fake_generate)
    manager = FakeFlowManager(question_index=3, current_node="question_4")
    flow._start_feedback(manager)
    await manager.state["feedback_task"]
    return asked["model"]


async def test_the_feedback_model_defaults_to_one_that_answers(monkeypatch) -> None:
    # gemini-3.8-flash returned 503 on every attempt during the first live session.
    monkeypatch.delenv("GEMINI_FEEDBACK_MODEL", raising=False)
    assert await _captured_feedback_model(monkeypatch) == "gemini-3.1-flash-lite"


async def test_the_feedback_model_is_still_overridable(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_FEEDBACK_MODEL", "gemini-3.8-flash")
    assert await _captured_feedback_model(monkeypatch) == "gemini-3.8-flash"


async def test_a_failed_feedback_call_still_speaks_something(monkeypatch) -> None:
    # The fixed line is kept so a 503 never ends the session in silence.
    import google.genai

    def exploding_client(*args, **kwargs):
        raise RuntimeError("503 UNAVAILABLE")

    monkeypatch.setattr(google.genai, "Client", exploding_client)
    text = await flow.generate_feedback("Coach: hi", "gemini-3.1-flash-lite", "key")
    assert text == prompts.FEEDBACK_FALLBACK


async def test_empty_feedback_also_falls_back(monkeypatch) -> None:
    import google.genai

    class BlankResponse:
        text = "   "

    class BlankClient:
        def __init__(self, *args, **kwargs) -> None:
            self.aio = self

        @property
        def models(self):
            return self

        async def generate_content(self, **kwargs):
            return BlankResponse()

    monkeypatch.setattr(google.genai, "Client", BlankClient)
    text = await flow.generate_feedback("Coach: hi", "gemini-3.1-flash-lite", "key")
    assert text == prompts.FEEDBACK_FALLBACK
