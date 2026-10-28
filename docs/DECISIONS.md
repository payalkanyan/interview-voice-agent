# Decisions

Dated entries for every provider, setting or approach that changed, with what prompted it.

## 2026-10-28: Pipecat Flows comes from `pipecat-ai`, not `pipecat-ai-flows`

**Changed:** No separate flows dependency. The import is `pipecat.flows`.

**Why:** The spec names "Pipecat Flows", which was a standalone package. In Pipecat 1.14.0 it
ships inside `pipecat-ai`; `flows/__init__.py:53` exports `FlowManager`, `Flow`, `FlowConfig` and
`NodeConfig`. Installing `pipecat-ai-flows` alongside would have pulled in a second, older copy.

**Measurement:** `python -c "import pipecat.flows"` succeeds with only `pipecat-ai` installed.

## 2026-10-28: TTS model is `sonic-3.6`, not `sonic-3`

**Changed:** `CARTESIA_MODEL=sonic-3.6`.

**Why:** The spec says `sonic-3`. In the installed service, `sonic-3` names a model *series*
(`services/cartesia/tts.py:62`), and the service's own examples pass `sonic-3.6`
(`services/cartesia/tts.py:420`). No default is hardcoded, so an exact endpoint has to be chosen.

**Measurement:** Read from the installed source. Not yet confirmed against a live synthesis call.

## 2026-10-28: Turn detection is Pipecat's default; the word floor is the real choice

**Changed:** `INTERRUPT_MIN_WORDS=2`. Everything else left at library defaults.

**Why:** `UserTurnStrategies` already defaults to
`stop=[TurnAnalyzerUserTurnStopStrategy(LocalSmartTurnAnalyzerV3())]`
(`turns/user_turn_strategies.py:45-54`), which is exactly the spec's turn detection. What the
spec adds is the two-word floor on interruptions, and `MinWordsUserTurnStartStrategy` applies its
threshold only while the bot is speaking (`min_words if self._bot_speaking else 1`), so one
strategy covers both "two words to interrupt" and "one word starts a normal turn".

**Measurement:** Defaults read from the installed source and asserted in `tests/test_bot.py`.
Not yet tuned against a recorded conversation.

## 2026-10-28: Questions are spoken by an action, not generated

**Changed:** Each question node uses a `tts_say` pre-action with `respond_immediately=False`.

**Why:** "Word for word" cannot be guaranteed by asking a model to repeat a string. It also freed
the node's task message to do one job, reacting to the answer, instead of deciding whether it was
time to ask or time to react.

**Measurement:** `tests/test_flow.py::test_questions_are_spoken_word_for_word` asserts each node
speaks its question exactly.

## 2026-10-28: Follow-up constraint is retired on advance

**Changed:** Added `FOLLOW_UP_RESET`, written into the context whenever the interview advances
with the budget spent.

**Why:** The injected constraint says "this question" but persisted for the whole session, so
after one follow-up on question one, questions two to four silently lost theirs.

**Measurement:** `tests/test_flow.py::test_advancing_restores_the_budget_for_the_next_question`
fails without it.

## 2026-10-28: Cartesia configured through `settings=`, not constructor arguments

**Changed:** `CartesiaTTSService(api_key=..., settings=Settings(model=..., voice=...))`.

**Why:** Passing `voice_id=` and `model=` directly raised `DeprecationWarning` (deprecated since
0.0.105, removed in 2.0.0), and it was inconsistent with how Deepgram and Google are configured.

**Measurement:** Constructing all three services now emits zero deprecation warnings from our
own code.

## 2026-10-28: `gemini-3.8-flash` was unavailable during development

**Changed:** Nothing. The feedback model is still `gemini-3.8-flash`.

**Why:** Recorded because it is a live risk, not a change. Every call during development returned
`503 UNAVAILABLE` ("This model is currently experiencing high demand"), both from
`scripts/llm_ttft.py` and from the eval's judge. The existing fixed fallback line keeps a session
from ending in silence, but it is generic, so a 503 at the wrong moment costs the specificity the
spec asks of feedback. No retry was added, because the spec puts fallbacks out of scope.

**Measurement:** 5 of 5 attempts failed with 503. The eval judge succeeded against
`gemini-3.5-flash-lite` on the same key, so this is model capacity, not credentials.

## 2026-10-28: TTFT measurements from this machine are unusable

**Changed:** Nothing. `gemini-3.1-flash-lite` stays the conversation model.

**Why:** `scripts/llm_ttft.py` gave medians of 3636 ms (`gemini-3.1-flash-lite`) and 3281 ms
(`gemini-3.5-flash-lite`) over 5 trials, with samples from 1099 ms to 12759 ms. A 12-second worst
case for a Flash-Lite model is network, and the spread exceeds the gap between the models, so the
comparison says nothing. The choice remains the spec's, unmeasured.

**Measurement:** The above, which is the point: it needs re-running from the demo machine.
