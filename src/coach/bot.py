"""Voice mock interview coach: the Pipecat pipeline and its entry point."""

import os

from dotenv import load_dotenv
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
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
from pipecat.workers.runner import WorkerRunner

load_dotenv()

PLACEHOLDER_PROMPT = (
    "You are a calm, friendly senior engineer running a short mock interview. "
    "Keep your replies to one or two short sentences."
)


def _env(name: str, default: str) -> str:
    """Read a setting from the environment, falling back to its documented default."""
    return os.getenv(name) or default


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

    context = LLMContext(
        messages=[{"role": "system", "content": PLACEHOLDER_PROMPT}],
    )
    aggregators = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            aggregators.user(),
            llm,
            tts,
            transport.output(),
            aggregators.assistant(),
        ]
    )

    worker = PipelineWorker(pipeline)
    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(worker)

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client) -> None:
        logger.info("Client connected; starting interview")

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client) -> None:
        logger.info("Client disconnected; ending session")
        await worker.cancel()

    await runner.run()


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
