"""Voice mock interview coach: the Pipecat pipeline and its entry point."""

import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger
from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.flows import FlowManager
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.google.llm import GoogleLLMService
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.turns.user_start.min_words_user_turn_start_strategy import (
    MinWordsUserTurnStartStrategy,
)
from pipecat.turns.user_stop.turn_analyzer_user_turn_stop_strategy import (
    TurnAnalyzerUserTurnStopStrategy,
)
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.workers.runner import WorkerRunner

from coach import flow
from coach.transcript import TranscriptRecorder

load_dotenv()


def _env(name: str, default: str) -> str:
    """Read a setting from the environment, falling back to its documented default."""
    return os.getenv(name) or default


def make_session_dir() -> Path:
    """Create the directory holding this session's transcript and latency log."""
    root = Path(_env("LOG_DIR", "logs"))
    path = root / f"session-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _env_float(name: str, default: float) -> float:
    """Read a numeric tunable from the environment."""
    raw = os.getenv(name)
    return float(raw) if raw else default


def _env_int(name: str, default: int) -> int:
    """Read an integer tunable from the environment."""
    raw = os.getenv(name)
    return int(raw) if raw else default


def _require_env(name: str) -> str:
    """Read a required API key, failing loudly rather than at connection time."""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is not set. Copy .env.example to .env and fill it in.")
    return value


def build_stt() -> DeepgramSTTService:
    """Build the Deepgram streaming STT service, boosted with interview keyterms."""
    keyterms = [t.strip() for t in _env("DEEPGRAM_KEYTERMS", "").split(",") if t.strip()]
    settings = DeepgramSTTService.Settings(
        model=_env("DEEPGRAM_MODEL", "nova-3-general"),
        keyterm=keyterms or None,
    )
    return DeepgramSTTService(api_key=_require_env("DEEPGRAM_API_KEY"), settings=settings)


def build_llm() -> GoogleLLMService:
    """Build the conversation LLM. The feedback model is a separate, stronger call."""
    model = _env("GEMINI_CONVERSATION_MODEL", "gemini-3.1-flash-lite")
    settings = GoogleLLMService.Settings(model=model)
    return GoogleLLMService(api_key=_require_env("GOOGLE_API_KEY"), settings=settings)


def build_tts() -> CartesiaTTSService:
    """Build the Cartesia websocket TTS service."""
    return CartesiaTTSService(
        api_key=_require_env("CARTESIA_API_KEY"),
        voice_id=_require_env("CARTESIA_VOICE_ID"),
        model=_env("CARTESIA_MODEL", "sonic-3.6"),
    )


def build_turn_strategies() -> UserTurnStrategies:
    """Build turn detection: Silero VAD stop timing plus Smart Turn v3, with a word floor.

    MinWordsUserTurnStartStrategy only applies its threshold while the bot is speaking, so a
    cough or "mm hmm" cannot interrupt, while a single word still starts a normal turn.
    """
    smart_turn = LocalSmartTurnAnalyzerV3(
        params=SmartTurnParams(
            stop_secs=_env_float("SMART_TURN_STOP_SECS", 3.0),
            pre_speech_ms=_env_float("SMART_TURN_PRE_SPEECH_MS", 500.0),
            max_duration_secs=_env_float("SMART_TURN_MAX_DURATION_SECS", 8.0),
        )
    )
    return UserTurnStrategies(
        start=[MinWordsUserTurnStartStrategy(min_words=_env_int("INTERRUPT_MIN_WORDS", 2))],
        stop=[TurnAnalyzerUserTurnStopStrategy(turn_analyzer=smart_turn)],
    )


def build_vad() -> SileroVADAnalyzer:
    """Build the Silero VAD analyzer that gates speech start and stop."""
    return SileroVADAnalyzer(
        params=VADParams(
            confidence=_env_float("VAD_CONFIDENCE", 0.7),
            start_secs=_env_float("VAD_START_SECS", 0.2),
            stop_secs=_env_float("VAD_STOP_SECS", 0.2),
            min_volume=_env_float("VAD_MIN_VOLUME", 0.6),
        )
    )


async def bot(runner_args: RunnerArguments) -> None:
    """Run one interview session. Called by Pipecat's dev runner per connection."""
    transport = await create_transport(
        runner_args,
        {
            "webrtc": lambda: TransportParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
            )
        },
    )
    await run_session(transport, runner_args)


async def run_session(transport: BaseTransport, runner_args: RunnerArguments) -> None:
    """Assemble and run the cascaded STT -> LLM -> TTS pipeline for one session."""
    stt = build_stt()
    llm = build_llm()
    tts = build_tts()

    # The persona and the per-question tasks are set by the flow's nodes, so the context
    # starts empty.
    context = LLMContext()
    aggregators = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=build_vad(),
            user_turn_strategies=build_turn_strategies(),
        ),
    )

    limiter = flow.FollowUpLimiter(context)

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            aggregators.user(),
            llm,
            limiter,
            tts,
            transport.output(),
            aggregators.assistant(),
        ]
    )

    session_dir = make_session_dir()
    logger.info(f"Session artifacts will be written to {session_dir}")
    transcript = TranscriptRecorder(session_dir)

    worker = PipelineWorker(pipeline, observers=[transcript])
    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(worker)

    flow_manager = FlowManager(
        llm=llm,
        context_aggregator=aggregators,
        worker=worker,
        transport=transport,
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client) -> None:
        logger.info("Client connected; starting interview")
        flow_manager.state["question_index"] = 0
        flow_manager.state["context"] = context
        await flow_manager.initialize(flow.initial_node())

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client) -> None:
        logger.info("Client disconnected; ending session")
        transcript.write()
        await worker.cancel()

    await runner.run()


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
