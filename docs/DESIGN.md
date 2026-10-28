# Design: verified Pipecat API surface

Everything below was read from the installed source at
`.venv/lib/python3.12/site-packages/pipecat/` (Pipecat **1.14.0**, Python 3.12.3). Paths in this
file are relative to that directory. This exists because Pipecat 1.x reshaped the pipeline, turn
and flow APIs, so no part of the build should be written from memory.

## Three corrections to docs/SPEC.md

| Spec says | Installed reality |
|---|---|
| "Pipecat Flows" (a separate package) | Ships **inside** `pipecat-ai` as `pipecat.flows` — `flows/__init__.py:53` exports `FlowManager`, `Flow`, `FlowConfig`, `NodeConfig`. The standalone `pipecat-ai-flows` package is not installed and not needed. |
| TTS model `sonic-3` | The service takes any `model` string, but its own examples use `sonic-3.6` (`services/cartesia/tts.py:420`). No default is hardcoded. `sonic-3` names a *series*, per `services/cartesia/tts.py:62`. We pass `sonic-3.6` and note it in the README. |
| Smart Turn v3 | `LocalSmartTurnAnalyzerV3` (`audio/turn/smart_turn/local_smart_turn_v3.py:28`) loads the bundled `smart-turn-v3.2-cpu.onnx` from `pipecat.audio.turn.smart_turn.data` when no path is given (`:48-51`). **No key and no download.** |

The Cartesia model string is the one open question: `sonic-3.6` is what the installed service
documents, so that is what we use rather than the spec's `sonic-3`.

## Pipeline shape

`LLMContext` (`processors/aggregators/llm_context.py:113`) holds the conversation.
`LLMContextAggregatorPair` (`processors/aggregators/llm_response_universal.py:2657`) wraps it and
exposes `.user()` and `.assistant()` for the two pipeline slots. `PipelineWorker`
(`pipeline/worker.py:201`) runs the pipeline and takes `observers: list[BaseObserver] | None`
(`pipeline/worker.py:~306`). `WorkerRunner` (`workers/runner.py:83`) drives it.

The dev runner's contract: the bot module defines a `bot(runner_args)` function, which
`pipecat.runner.run.main()` (`runner/run.py:1878`) discovers via `_get_bot_module()`
(`runner/run.py:532`). `create_transport(runner_args, transport_params)`
(`runner/utils.py:684`) builds the transport from a `{"webrtc": lambda: TransportParams(...)}`
factory map. `SmallWebRTCTransport` is at `transports/smallwebrtc/transport.py:1027`.

## Turn-taking

Turn behaviour is configured through `LLMUserAggregatorParams`
(`processors/aggregators/llm_response_universal.py:129`), not on the transport. The fields we set
are `user_turn_strategies`, `vad_analyzer` and `user_idle_timeout` (`:188-199`).

`UserTurnStrategies` (`turns/user_turn_strategies.py:57`) holds `start` and `stop` lists. Its
defaults (`:29-54`) are already `start=[VADUserTurnStartStrategy, TranscriptionUserTurnStartStrategy]`
and `stop=[TurnAnalyzerUserTurnStopStrategy(LocalSmartTurnAnalyzerV3())]` — so the spec's turn
detection is Pipecat's default, and what we actually add is the word floor on interruptions.

- `MinWordsUserTurnStartStrategy(min_words=...)` — `turns/user_start/min_words_user_turn_start_strategy.py:22`.
  Its `_handle_transcription` uses `min_words if self._bot_speaking else 1` (`:~90`). So **one word
  starts a normal turn and `min_words` is required only to interrupt**, which is exactly the spec's
  "a cough or mm hmm should not count as an interruption". It needs no companion strategy.
- `TurnAnalyzerUserTurnStopStrategy(turn_analyzer=...)` — `turns/user_stop/turn_analyzer_user_turn_stop_strategy.py:34`.
  Waits for the analyzer to report COMPLETE *and* a finalized transcript (or an STT timeout).
- `SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.6)` — `turns/user_stop/speech_timeout_user_turn_stop_strategy.py:27`.
  The simpler timer-based alternative; kept as a documented fallback, not used.

Tunable defaults, for the README's default/mine/why table:

| Setting | Default | Source |
|---|---|---|
| VAD `confidence` | 0.7 | `audio/vad/vad_analyzer.py:25` |
| VAD `start_secs` | 0.2 | `audio/vad/vad_analyzer.py:26` |
| VAD `stop_secs` | 0.2 | `audio/vad/vad_analyzer.py:27` |
| VAD `min_volume` | 0.6 | `audio/vad/vad_analyzer.py:28` |
| Smart turn `stop_secs`, `pre_speech_ms`, `max_duration_secs` | `SmartTurnParams` | `audio/turn/smart_turn/base_smart_turn.py:32-43` |
| `user_speech_timeout` | 0.6 | `turns/user_stop/speech_timeout_user_turn_stop_strategy.py:~52` |

`SileroVADAnalyzer(params=VADParams(...))` is at `audio/vad/silero.py:160`.

## Services

- **STT:** `DeepgramSTTService(api_key=..., settings=DeepgramSTTService.Settings(...))` —
  `services/deepgram/stt.py:292`, `Settings = DeepgramSTTSettings` at `:299`.
  `DeepgramSTTSettings` (`:179`) carries `keyterm` (`:212`) for the spec's keyterms, plus `model`
  and `language` inherited from `STTSettings`. `live_options=` is deprecated since 0.0.105 (`:~340`)
  — use `settings=`.
- **TTS:** `CartesiaTTSService(api_key=..., settings=CartesiaTTSService.Settings(...))` —
  `services/cartesia/tts.py:310`, constructor at `:335`, `Settings = CartesiaTTSSettings` at `:318`.
  The settings fields are `model`, `voice`, `language`, `extra`, `generation_config` and
  `pronunciation_dict_id`. Passing `voice_id=`/`model=` to the constructor still works but is
  deprecated since 0.0.105. WebSocket streaming with word timestamps.
- **LLM:** `GoogleLLMService(api_key=..., settings=GoogleLLMService.Settings(model=...))` —
  `services/google/llm.py:205`, constructor at `:255`. Bare `model=` is deprecated since 0.0.105
  (`:277`). The installed `_THINKING_DEFAULTS` map (`:81-99`) already knows both of our models:
  `gemini-3.1-flash-lite` → `thinking_level: "minimal"`, `gemini-3.8-flash` → `thinking_level: "low"`.
  So both model IDs are recognised by this Pipecat version.

## Flows

`NodeConfig` (`flows/types.py:182`) is a `TypedDict`: `task_messages` (required), plus `name`,
`role_message`, `functions`, `pre_actions`, `post_actions`, `context_strategy` and
`respond_immediately` (`:224-237`). `role_message` is a plain string sent as the system instruction
and **persists across transitions** until another node sets it (`:189-192`) — so the persona is set
once on the greeting node.

`FlowManager(llm=..., context_aggregator=..., worker=..., transport=...)` —
`flows/manager.py:107`. Relevant members:

- `initialize(initial_node)` — `flows/manager.py:307`
- `set_node_from_config(node_config)` — `flows/manager.py:608`
- `state` — `flows/manager.py:172`, a dict persisting across node transitions. **This is where
  the question index and the per-question follow-up flag live**, which is how the spec's "held in
  code, not only in the prompt" is satisfied.

A node function is a *direct function*: first parameter `flow_manager`, remaining parameters become
the tool's schema, derived from the signature and Google-style docstring
(`flows/types.py:468` `FlowsDirectFunctionWrapper`). It returns
`ConsolidatedFunctionResult = tuple[Any, NodeConfig | None | _NoResponse]` (`flows/types.py:283`):
the first element is the result given to the LLM, the second is the next node, `None` to stay, or
`NO_RESPONSE` to neither transition nor respond.

## Observers

`BaseObserver` (`observers/base_observer.py:114`) with `__init__(*, observe_every_push=False)`
(`:129`) and `async def on_push_frame(self, data: FramePushed)`. `FramePushed`
(`observers/base_observer.py:48`) carries `source`, `destination`, `frame`, `direction`,
`timestamp` (**nanoseconds, from the pipeline clock**, `:61`) and `first_push` (`:62`).

`observe_every_push=False` is what the latency observer wants: a frame is re-pushed by every
processor that passes it along, and we want each moment once.

## Frames for latency and transcript

All in `frames/frames.py`:

| Frame | Line | Use |
|---|---|---|
| `VADUserStoppedSpeakingFrame` | 1504 | **`user_stopped_ms`.** Carries `stop_secs` and `timestamp`, so the true moment the user went quiet is `timestamp - stop_secs`. |
| `UserStoppedSpeakingFrame` | 1408 | **`turn_end_ms`** — "the user turn has ended", i.e. when end-of-turn was decided. |
| `TranscriptionFrame` | 586 | `stt_final_ms`, and the user's words for the transcript. |
| `InterimTranscriptionFrame` | 612 | Feeds the min-words interruption check. |
| `LLMTextFrame` | 387 | `llm_first_token_ms`. |
| `TTSAudioRawFrame` | 285 | `tts_first_audio_ms`. |
| `TTSTextFrame` | 518 | Coach words, emitted **in strict playback order**. |
| `AggregatedTextProgressFrame` | 536 | `accumulated_text` / `remaining_text` per word (`:555-559`). |
| `InterruptionFrame` | 1385 | Marks a turn interrupted. |
| `BotStartedSpeakingFrame` / `BotStoppedSpeakingFrame` | 1555 / 1566 | Bot turn boundaries. |

Two consequences worth stating plainly:

**The spec's `user_stopped_ms` is measurable properly.** `VADUserStoppedSpeakingFrame` is distinct
from `UserStoppedSpeakingFrame`, and subtracting its `stop_secs` removes the VAD's own silence
window. So the turn-detection wait and the LLM wait really can be separated, as the spec asks.

**The interrupted-transcript requirement is satisfiable exactly, not approximately.** This was the
plan's main open risk. `add_word_timestamps` (`services/tts_service.py:881`) routes word timestamps
"into the per-context audio queue alongside audio frames so they are processed in strict playback
order" (`:890-893`), and Cartesia supplies them (`services/cartesia/tts.py:799-806`). Accumulating
`TTSTextFrame` text and cutting at `InterruptionFrame` therefore yields the words actually spoken —
no estimate needed.

**`tts_first_audio_ms` remains an approximation.** `TTSAudioRawFrame` is observed as it is pushed
toward the transport, which is server send time, not the moment sound leaves the speaker. The
browser playout and WebRTC jitter buffer add an unmeasured gap on top. The README says so and
estimates the gap, per the spec.

## Not present in 1.14.0

There is no `TranscriptProcessor` in this version (no match under `processors/`, and
`transcriptions/` holds only `language.py`). The plan assumed one. So `src/coach/transcript.py`
builds the transcript from the frames in the table above instead — which is what the
`[INTERRUPTED]` requirement needs anyway, since it depends on `TTSTextFrame` playback order.
