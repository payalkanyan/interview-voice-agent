"""The interview flow.

Which question we are on lives in ``flow_manager.state``, and each question is a separate node,
so the order in docs/SPEC.md is guaranteed by the graph rather than by asking the LLM nicely.

Each question is spoken by a ``tts_say`` pre-action rather than generated, which is what makes
"word for word" literally true. The node's LLM turn only ever reacts to the answer.
"""

from loguru import logger
from pipecat.flows import ConsolidatedFunctionResult, FlowManager, NodeConfig

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


def closing_node() -> NodeConfig:
    """Build the node that ends the interview."""
    return {
        "name": "closing",
        "task_messages": [{"role": "developer", "content": "The interview is over. Say nothing."}],
        "pre_actions": [{"type": "tts_say", "text": prompts.GOODBYE}],
        "post_actions": [{"type": "end_conversation"}],
        "respond_immediately": False,
    }


async def next_question(flow_manager: FlowManager) -> ConsolidatedFunctionResult:
    """Move on to the next interview question.

    Call this once the candidate's answer to the current question is good enough.
    """
    index = flow_manager.state.get("question_index", 0) + 1
    flow_manager.state["question_index"] = index

    if index >= QUESTION_COUNT:
        logger.info("All questions answered; closing")
        return None, closing_node()

    logger.info(f"Moving to question {index + 1} of {QUESTION_COUNT}")
    return None, question_node(index)


def initial_node() -> NodeConfig:
    """Build the node the interview starts in: the greeting plus question one."""
    return question_node(0)
