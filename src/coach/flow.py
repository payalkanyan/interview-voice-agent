"""The interview flow.

Which question we are on lives in ``flow_manager.state``, and each question is a separate node,
so the order in docs/SPEC.md is guaranteed by the graph rather than by asking the LLM nicely.

Each question is spoken by a ``tts_say`` pre-action rather than generated, which is what makes
"word for word" literally true. The node's LLM turn only ever reacts to the answer.
"""

import asyncio
import os
import re
from collections.abc import Awaitable, Callable

from loguru import logger
from pipecat.flows import ConsolidatedFunctionResult, FlowManager, NodeConfig
from pipecat.frames.frames import (
    Frame,
    FunctionCallsStartedFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TTSSpeakFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from coach import prompts

QUESTION_COUNT = len(prompts.QUESTIONS)


def question_node(index: int) -> NodeConfig:
    """Build the node for question ``index``, speaking the question verbatim on entry."""
    question = prompts.QUESTIONS[index]
    spoken = f"{prompts.GREETING} {question}" if index == 0 else question

    node: NodeConfig = {
        "name": f"question_{index + 1}",
        "task_messages": [{"role": "developer", "content": prompts.reaction_task(question)}],
        "pre_actions": [{"type": "tts_say", "text": spoken}],
        "functions": [next_question],
        # The question is already spoken by the pre-action, so the LLM waits for the answer
        # instead of talking over it.
        "respond_immediately": False,
    }
    if index == 0:
        node["role_message"] = prompts.PERSONA
    return node


def _start_feedback(flow_manager: FlowManager) -> None:
    """Start the feedback call now, so the bridge line is spoken while it runs."""
    context = flow_manager.state.get("context")
    if context is None:
        logger.error("No context registered; feedback will use the fallback line")
        return

    transcript = transcript_for_feedback(context)
    model = os.getenv("GEMINI_FEEDBACK_MODEL") or "gemini-3.1-flash-lite"
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY") or ""
    flow_manager.state["feedback_task"] = asyncio.create_task(
        generate_feedback(transcript, model, api_key)
    )


async def next_question(flow_manager: FlowManager) -> ConsolidatedFunctionResult:
    """Move on to the next interview question.

    Call this once the candidate's answer to the current question is good enough.
    """
    index = flow_manager.state.get("question_index", 0) + 1
    flow_manager.state["question_index"] = index

    limiter = flow_manager.state.get("limiter")
    if limiter is not None:
        limiter.set_question_index(index)

    if index >= QUESTION_COUNT:
        logger.info("All questions answered; generating feedback")
        _start_feedback(flow_manager)
        return None, feedback_node()

    logger.info(f"Moving to question {index + 1} of {QUESTION_COUNT}")
    return None, question_node(index)


async def force_advance(flow_manager: FlowManager) -> None:
    """Advance the interview from code, when the LLM finished a turn without advancing.

    The model is asked to call ``next_question`` once an answer is good enough, and if it simply
    does not, the interview stalls where it stands: in the first live session it ended on a
    goodbye of its own invention and the feedback node was never reached. This is the backstop,
    so reaching the feedback is not contingent on the model choosing to.
    """
    if flow_manager.current_node == "feedback":
        return

    logger.warning("Coach stalled without calling next_question; advancing from code")
    _, node = await next_question(flow_manager)
    if node is not None:
        await flow_manager.set_node_from_config(node)


def initial_node() -> NodeConfig:
    """Build the node the interview starts in: the greeting plus question one."""
    return question_node(0)


def _normalize(text: str) -> str:
    """Reduce spoken text to comparable words, so the nudge can be recognised."""
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", text.lower()).split())


class FollowUpLimiter(FrameProcessor):
    """Hold the one-follow-up-per-question cap in code.

    A follow-up is not a tool call, it is just the coach speaking, so the cap cannot be enforced
    by the flow graph. This processor watches each coach reply instead: a reply that calls
    next_question moves the interview on, a reply matching the nudge line is a nudge, and
    anything else is the question's one follow-up. Once that is spent, the processor writes a
    developer message into the context so the constraint reaches the next turn from code rather
    than from the node prompt alone.
    """

    def __init__(
        self,
        context: LLMContext,
        on_stalled: Callable[[], Awaitable[None]] | None = None,
        **kwargs,
    ) -> None:
        """Initialize the limiter against the context it will write constraints into.

        Args:
            context: The LLM context to write follow-up constraints into.
            on_stalled: Called when a reply finishes with the follow-up already spent and no
                ``next_question`` call, meaning the interview would otherwise stall here.
        """
        super().__init__(**kwargs)
        self._context = context
        self._on_stalled = on_stalled
        self._nudge = _normalize(prompts.NUDGE)
        self._reply = ""
        self._advanced = False
        self._follow_up_used = False
        self._question_index = 0

    @property
    def follow_up_used(self) -> bool:
        """Whether the current question has already had its one follow-up."""
        return self._follow_up_used

    @property
    def question_index(self) -> int:
        """Which question the coach is currently on, counting from zero."""
        return self._question_index

    def set_question_index(self, index: int) -> None:
        """Tell the limiter which question is live. Called by ``next_question``."""
        self._question_index = index

    def _current_question(self) -> str | None:
        """The question being asked right now, or None once the questions run out."""
        if 0 <= self._question_index < QUESTION_COUNT:
            return prompts.QUESTIONS[self._question_index]
        return None

    def _is_reask(self, reply: str) -> bool:
        """Whether this reply is the coach asking the current question again.

        A question spoken by a ``tts_say`` pre-action can land while the candidate is still
        finishing the previous answer, so the leftover fragment arrives as the answer. The coach
        re-asking in that case is doing the right thing, and must not be charged a follow-up.
        """
        question = self._current_question()
        return bool(question) and reply.endswith(_normalize(question))

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Track coach replies and spend the follow-up budget."""
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMFullResponseStartFrame):
            self._reply = ""
            self._advanced = False
        elif isinstance(frame, FunctionCallsStartedFrame):
            # next_question is the only tool, so a call here means a new question is starting.
            self._advanced = True
            if self._follow_up_used:
                # The spent-budget message named "this question"; retire it so it cannot
                # suppress a legitimate follow-up on the next one.
                self._context.add_message({"role": "developer", "content": prompts.FOLLOW_UP_RESET})
            self._follow_up_used = False
        elif isinstance(frame, LLMTextFrame):
            self._reply += frame.text
        elif isinstance(frame, LLMFullResponseEndFrame):
            self._spend_follow_up()

        await self.push_frame(frame, direction)

    def _begin_advance(self) -> None:
        """Note that the interview is moving on, and restore the follow-up budget."""
        self._advanced = True
        if self._follow_up_used:
            # The spent-budget message named "this question"; retire it so it cannot suppress
            # a legitimate follow-up on the next one.
            self._context.add_message({"role": "developer", "content": prompts.FOLLOW_UP_RESET})
        self._follow_up_used = False

    def _schedule_stall(self) -> None:
        """Hand the forced advance to the task manager.

        Scheduled rather than awaited: the transition queues frames through this same pipeline,
        so awaiting it inside ``process_frame`` would deadlock.
        """
        if self._on_stalled is None:
            return
        try:
            self.create_task(self._on_stalled())
        except Exception as exc:
            # A failed backstop must not take the conversation down with it.
            logger.error(f"Could not schedule the forced advance: {exc}")

    def _spend_follow_up(self) -> None:
        """Classify the reply that just finished and update the budget."""
        reply = _normalize(self._reply)
        if self._advanced or not reply:
            return
        if reply == self._nudge:
            logger.info("Coach nudged; follow-up budget untouched")
            return
        if self._is_reask(reply):
            logger.info("Coach re-asked the question; follow-up budget untouched")
            return
        if self._follow_up_used:
            # The follow-up is spent and the coach still did not move on, so the interview is
            # stuck on this question. Advance it in code rather than waiting on the model.
            logger.warning("Coach did not move on with its follow-up spent; forcing the advance")
            self._schedule_stall()
            return

        self._follow_up_used = True
        logger.info("Coach used its one follow-up for this question")
        self._context.add_message({"role": "developer", "content": prompts.FOLLOW_UP_SPENT})


def transcript_for_feedback(context: LLMContext) -> str:
    """Render the interview so far as a plain speaker-labelled transcript."""
    lines: list[str] = []
    for message in context.get_messages():
        role = message.get("role")
        content = message.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str):
            continue
        speaker = "Candidate" if role == "user" else "Coach"
        lines.append(f"{speaker}: {content.strip()}")
    return "\n".join(lines)


async def generate_feedback(transcript: str, model: str, api_key: str) -> str:
    """Generate the closing feedback with a stronger model, in a separate call.

    Returns a fixed fallback line if the call fails, so a session never ends in silence.
    """
    from google import genai
    from google.genai import types

    try:
        # Client construction is inside the guard too: a bad key or config must fall back to a
        # spoken line rather than end the session in silence.
        client = genai.Client(api_key=api_key)
        response = await client.aio.models.generate_content(
            model=model,
            contents=prompts.FEEDBACK_USER.format(transcript=transcript),
            config=types.GenerateContentConfig(
                system_instruction=prompts.FEEDBACK_SYSTEM,
                temperature=0.6,
                max_output_tokens=2048,
            ),
        )
        text = (response.text or "").strip()
    except Exception as exc:
        logger.error(f"Feedback generation failed: {exc}")
        return prompts.FEEDBACK_FALLBACK

    if not text:
        logger.error("Feedback model returned nothing")
        return prompts.FEEDBACK_FALLBACK

    logger.info(f"Feedback generated by {model}: {text}")
    return text


async def speak_feedback(action: dict, flow_manager: FlowManager) -> None:
    """Speak the feedback the stronger model generated while the bridge line was playing."""
    task = flow_manager.state.get("feedback_task")
    if task is None:
        logger.error("No feedback task was started")
        text = prompts.FEEDBACK_FALLBACK
    else:
        text = await task

    await flow_manager.worker.queue_frame(TTSSpeakFrame(text=text, append_to_context=True))


def feedback_node() -> NodeConfig:
    """Build the closing node: bridge line, then spoken feedback, then goodbye.

    The feedback call is already in flight by the time this node is entered (see
    ``next_question``), so the bridge line covers the wait instead of following it.
    """
    return {
        "name": "feedback",
        "task_messages": [{"role": "developer", "content": "The interview is over. Say nothing."}],
        "pre_actions": [
            {"type": "tts_say", "text": prompts.BRIDGE},
            {"type": "function", "handler": speak_feedback},
            {"type": "end_conversation", "text": prompts.GOODBYE},
        ],
        "respond_immediately": False,
    }
