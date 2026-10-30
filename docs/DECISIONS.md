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

## 2026-10-30: Feedback model moved to `gemini-3.1-flash-lite`

**Changed:** `GEMINI_FEEDBACK_MODEL` now defaults to `gemini-3.1-flash-lite`, and
`EVAL_JUDGE_MODEL` with it. Client construction moved inside the error guard in
`generate_feedback`.

**Why:** `gemini-3.8-flash` returned `503 UNAVAILABLE` on every attempt across two days, so the
"stronger model in a separate call" the spec asks for produced nothing at all. A weaker model that
answers beats a stronger one that does not. The fixed fallback line is kept for when even this
fails.

**Measurement:** 5 of 5 TTFT attempts and the eval judge all failed with 503 on
`gemini-3.8-flash`. `gemini-3.1-flash-lite` returned usable two-sentence feedback on the same key
during testing.

## 2026-10-30: A re-ask does not spend the follow-up budget

**Changed:** Added a RE-ASK branch to the reaction task, plus question-index tracking and re-ask
recognition in `FollowUpLimiter`.

**Why:** Questions are spoken by a `tts_say` pre-action on node entry, which does not wait for the
candidate to stop talking. In the first live session question four played 0.8 seconds after the
coach was interrupted, and the leftover fragment "and other benchmarks." was taken as the answer.
Re-asking is the right recovery, so it must not cost the question its follow-up.

**Measurement:** `results/first-call/transcript.txt`, 01:54.118 to 01:55.699.

## 2026-10-30: Reaching the feedback is a code backstop, not a prompt instruction

**Changed:** Added `force_advance`, called via an `on_stalled` callback when a reply finishes with
the follow-up spent and no `next_question` call. The prompt also now forbids goodbyes.

**Why:** In the first live session the coach ended question four with "Thanks for sharing those
details with me today.", never called `next_question`, and the session ended with no feedback at
all. The spec requires feedback, so it cannot be contingent on the model choosing to advance.

**Measurement:** `results/first-call/transcript.txt`, 02:16.217 is the last line of the session.

## 2026-10-30: Latency rows are keyed on coach replies

**Changed:** `LatencyObserver` holds user-side timings as pending values and opens a row on the
first `LLMTextFrame` of a reply, rather than on every `VADUserStartedSpeakingFrame`.

**Why:** Keying on speech starts gave 74 rows for about 8 real turns, 37 entirely null, because
every pause and blip opened one. Worse, rows opened before the coach replied could collect a
scripted question's audio, which is how 3 rows ended up with `tts_first_audio_ms` earlier than
`user_stopped_ms`. A reply is the first moment a turn is known to be real, and scripted `tts_say`
questions produce no LLM text, so they open no row.

**Measurement:** `results/first-call/latency.jsonl` has 74 rows, 37 null, 9 with both endpoints,
3 inverted. The replacement is covered by `tests/test_latency_observer.py`, which drives synthetic
frames shaped like that session and asserts one row per reply.

## 2026-10-30: STT is reported as an offset, not a pipeline stage

**Changed:** `scripts/latency_report.py` stages are now user-stopped to turn-end, turn-end to
first token, and first token to first audio. The final transcript is reported separately as an
offset from the end-of-turn decision.

**Why:** The old stage list assumed a strict chain through STT, but
`TurnAnalyzerUserTurnStopStrategy` waits for whichever of the turn analyzer and the finalized
transcript is *later*, so STT frequently completes first and the "stt final" stage came out
negative. The stages now add up to the total.

**Measurement:** Turn 4 of the first session has `stt_final_ms` 14282 against `turn_end_ms` 15928,
an offset of -1646 ms.
