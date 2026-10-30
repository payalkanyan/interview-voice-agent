# Voice mock interview coach

A voice agent that runs a short mock interview in the browser. It asks four fixed questions in
order, reacts to each answer, and closes with spoken feedback. Built with Pipecat as a cascaded
pipeline: separate STT, LLM and TTS, no speech-to-speech model.

## Run it

```bash
make setup                 # create the venv with uv and install
cp .env.example .env       # then fill in the three API keys
make run                   # open http://localhost:7860/client
```

You need keys for Deepgram, Cartesia and Google AI Studio, plus a Cartesia voice id. Smart Turn
v3 needs no key: Pipecat bundles the model weights.

| Command | What it does |
|---|---|
| `make setup` | Create `.venv` with uv and install the project |
| `make run` | Start the bot and serve the browser client |
| `make test` | Run the test suite |
| `make lint` | `ruff check` and `ruff format` |
| `make report SESSION=logs/session-<time>` | Latency percentiles and per-stage averages |
| `make eval SESSION=logs/session-<time>` | Rule checks and LLM judge over one transcript |

Each session writes `transcript.txt`, `transcript.json` and `latency.jsonl` into its own
`logs/session-<timestamp>/` directory.

## Architecture

```
browser mic
  -> SmallWebRTC transport
  -> STT: Deepgram nova-3 (streaming, keyterms)
  -> turn detection: Silero VAD + Smart Turn v3 (local ONNX)
  -> LLM: Gemini 3.1 Flash-Lite (streaming)
  -> TTS: Cartesia sonic-3.6 (websocket)
  -> browser speaker
```

Five files do the work:

| File | Responsibility |
|---|---|
| [src/coach/bot.py](src/coach/bot.py) | Builds the services and the pipeline, reads every setting from `.env` |
| [src/coach/flow.py](src/coach/flow.py) | The four-question graph, the follow-up cap, the feedback call |
| [src/coach/prompts.py](src/coach/prompts.py) | Every word the coach can say that isn't generated |
| [src/coach/latency_observer.py](src/coach/latency_observer.py) | Per-turn timings, as an observer |
| [src/coach/transcript.py](src/coach/transcript.py) | The transcript, including interrupted lines |

[docs/DESIGN.md](docs/DESIGN.md) records the exact Pipecat API this is written against, with
`file:line` citations into the installed source, because Pipecat 1.x reshaped these APIs.

### Question order is structural, not requested

Each question is a Pipecat Flows node, and the question index lives in `flow_manager.state`. The
LLM cannot reorder or skip a question because it has exactly one tool, `next_question`, which
advances the graph.

The questions are also not generated. Each node speaks its question through a `tts_say`
pre-action, so "word for word" is literally true rather than something the model is asked to
honour. The node's LLM turn only ever reacts to the answer.

### One follow-up per question

Reacting is one LLM call, not two: a follow-up or a nudge is just the coach speaking, and only
moving on costs a tool call. That keeps the common case at a single round trip.

The cost is that a follow-up is invisible to the flow graph, since it isn't a tool call. So
[`FollowUpLimiter`](src/coach/flow.py) watches each finished coach reply: a reply that called
`next_question` is an advance, a reply matching the nudge line is a nudge, and anything else is
that question's one follow-up. Once spent, the limiter writes a developer message into the
context so the constraint reaches the next turn from code. Advancing retires that message and
restores the budget.

## Providers, and why

| Layer | Choice | Why |
|---|---|---|
| STT | Deepgram `nova-3-general` | Streaming with interim results, which the interruption rule needs, and `keyterm` boosting for interview vocabulary |
| Turn detection | Silero VAD + `LocalSmartTurnAnalyzerV3` | Smart Turn v3 judges end-of-turn from the waveform, not the transcript, so a mid-answer "um" pause does not read as a finished turn. Runs locally on CPU with bundled weights, so no extra key and no network hop |
| LLM | `gemini-3.1-flash-lite` | Cheapest fast model in the Gemini 3 line, and the reaction task is small: classify one answer and write one short sentence |
| Feedback LLM | `gemini-3.8-flash` | A stronger model in a separate call, as the spec asks. Same key and same SDK, so no second provider |
| TTS | Cartesia `sonic-3.6` | Websocket streaming with word timestamps, which is what makes an interrupted transcript line exact rather than estimated |
| Transport | SmallWebRTC | Runs locally, browser only, no account needed |

The one deviation from the spec: the TTS model string is `sonic-3.6`, not `sonic-3`. `sonic-3`
names a series rather than an endpoint, and `sonic-3.6` is what the installed Pipecat service
documents. See [docs/DECISIONS.md](docs/DECISIONS.md).

## Turn-taking

Every value is read from `.env`. The defaults below are Pipecat's own, cited to the installed
source in [docs/DESIGN.md](docs/DESIGN.md).

| Setting | Default | My value | Why |
|---|---|---|---|
| `VAD_CONFIDENCE` | 0.7 | 0.7 | Unchanged. Not yet shown to need changing |
| `VAD_START_SECS` | 0.2 | 0.2 | Unchanged |
| `VAD_STOP_SECS` | 0.2 | 0.2 | Unchanged. This only gates the VAD's own stop signal; Smart Turn decides the actual end of turn, so lengthening this would add latency without improving pause handling |
| `SMART_TURN_STOP_SECS` | 3.0 | 3.0 | Unchanged. This is the backstop that forces a turn end after silence, not the normal path |
| `SMART_TURN_PRE_SPEECH_MS` | 500.0 | 500.0 | Unchanged |
| `SMART_TURN_MAX_DURATION_SECS` | 8.0 | 8.0 | Unchanged. Smart Turn v3 takes at most 8 seconds of audio |
| `INTERRUPT_MIN_WORDS` | n/a | 2 | The spec's rule. `MinWordsUserTurnStartStrategy` applies this threshold **only while the coach is speaking**, so two words are needed to interrupt but one word still starts a normal turn. A cough or "mm hmm" cannot cut the coach off |

**These are starting points, not tuned values.** Only `INTERRUPT_MIN_WORDS` is a deliberate
choice; the rest are library defaults that have not yet been measured against a real
conversation. Tuning them is what a recorded session is for, and this table should be updated
from that session.

### What handles each scenario

| Scenario | Mechanism |
|---|---|
| A, a normal answer | Smart Turn v3 reports the turn complete, then the turn waits for Deepgram's finalized transcript |
| B, a mid-answer pause | Smart Turn v3 reads the waveform, so "I worked on... um... a payments service" is not a finished turn. `SMART_TURN_STOP_SECS` is the 3-second backstop behind it |
| C, an interruption | `MinWordsUserTurnStartStrategy(min_words=2)` gates barge-in while the coach speaks. The coach stops, answers what was said, and the flow returns to the current question because the node never changed |

## Latency

`latency.jsonl` gets one line per user turn:

```json
{"turn": 1, "user_stopped_ms": 9000, "turn_end_ms": 9450, "stt_final_ms": 9500,
 "llm_first_token_ms": 9780, "tts_first_audio_ms": 9910, "interrupted": false}
```

All `_ms` values are milliseconds from the start of the session. Two details matter for reading
them honestly:

- **`user_stopped_ms` is when the user actually went quiet**, not when end of turn fired. Pipecat
  reports those as two separate frames, and `VADUserStoppedSpeakingFrame` carries the `stop_secs`
  silence window it needed to make the call, which is subtracted back out. `turn_end_ms` is the
  end-of-turn decision, so the turn-detection wait and the LLM wait can be read apart.
- **`tts_first_audio_ms` is server send time, not playback.** It is when the first audio frame was
  pushed to the transport. The browser's jitter buffer and playout add a further gap that this
  process cannot see, so true time-to-sound is somewhat later than every number here. The spec
  asks for this to be said plainly, and this is it.

The logging is done by an observer, so it reads frames without sitting in the pipeline's path,
and every line is buffered in memory and written once at the end of the session.

### Measured numbers

**There is still no usable set of numbers.** The first live session is in
[results/first-call/](results/first-call/), but its `latency.jsonl` was produced by the broken
observer: 74 rows for about 8 real turns, 37 of them entirely null, and 3 where the first audio
appeared to arrive before the user went quiet. Those numbers are not reported here because they
are wrong, not because they are missing. The observer is fixed and covered by synthetic-frame
tests, so the next session should produce one row per coach reply. To produce them:

```bash
make run                                   # run a full interview, doing scenarios A, B and C
make report SESSION=logs/session-<time>    # p50/p95 and per-stage averages
```

Then paste that output here and update the turn-taking table above with anything the session
showed needed changing.

The per-stage breakdown is additive: turn detection, LLM first token and TTS first audio sum to
the total. STT is reported separately as an offset from the end-of-turn decision, because
Deepgram often finalizes a transcript *before* end of turn is decided, so as a stage it would
read as negative time. A negative offset means the reply was not waiting on STT at all.

### Model latency

`scripts/llm_ttft.py` measures time to first token so the model choice can be checked rather than
assumed. Run from this machine it produced a median of 3636 ms for `gemini-3.1-flash-lite` and
3281 ms for `gemini-3.5-flash-lite` over 5 trials, with a spread from 1099 ms to 12759 ms.

**Those numbers are not usable.** A 12-second worst case for a Flash-Lite model is network, not
inference, and the spread is wider than the difference between the two models. They are recorded
here only to say that the comparison has not actually been made yet. Re-run it from the machine
that will host the demo.

## What's weak

- **One live session so far, and it has not been repeated.** The first call is in
  [results/first-call/](results/first-call/). Four bugs it exposed are fixed, but the fixes
  themselves have not met a real conversation yet, and the turn-taking values are still untuned.
  This is the main gap.
- **The feedback model is now `gemini-3.1-flash-lite`, not `gemini-3.8-flash`.** 3.8-flash
  returned `503 UNAVAILABLE` ("experiencing high demand") on every single attempt, so the
  feedback it was supposed to produce never existed. The default is now the model that actually
  answers, which is the same one used for the conversation. That gives up the spec's "stronger
  model" for one that responds; `GEMINI_FEEDBACK_MODEL` still points it anywhere. The fixed
  fallback line is kept, and client construction is now inside the error guard too, so a bad key
  falls back to speech rather than killing the session.
- **A question can still play over the candidate.** The `tts_say` pre-action fires on node entry
  whether or not the candidate has stopped talking; that is what made question four land on a
  leftover fragment. The coach now re-asks instead of accepting the fragment, which recovers but
  costs a turn.
- **Reaching the feedback now depends on a backstop, not on the model.** When a reply finishes
  with the follow-up spent and no `next_question` call, code advances the flow itself. The
  trade-off is that a question can be cut short if the model says something that is neither a
  nudge nor a verbatim re-ask.
- **Counting follow-ups in the eval is a heuristic.** It counts coach lines containing a question
  mark that are neither a fixed question nor the nudge. An acknowledgement phrased as a question
  would be miscounted. The LLM judge is there to cover the judgement the rule check cannot make.
- **`tts_first_audio_ms` understates time to sound**, as above.
- **The nudge is matched by text.** The limiter recognises a nudge by comparing the reply against
  the nudge line. If the model paraphrases it instead of saying it exactly, the reply is treated
  as a follow-up and spends the budget.
- **One session per process.** The session directory is created when a client connects; nothing
  cleans up or rotates `logs/`.

## AI tools

Built with Claude Code (Opus 5). The spec and the working agreement in `docs/CLAUDE.md` were
written by hand; the implementation was generated step by step, one commit per step, against a
plan reviewed before any code was written.

The rule that mattered most was in `docs/CLAUDE.md`: check the installed Pipecat source before
using any class, and say where it was found. Pipecat 1.x had moved nearly every API the spec
implied, and writing from memory would have produced code against the 0.x shape. That check
became [docs/DESIGN.md](docs/DESIGN.md), and it also turned up two things that improved the
result: `VADUserStoppedSpeakingFrame` is distinct from `UserStoppedSpeakingFrame`, which is what
makes `user_stopped_ms` honest, and Cartesia's word timestamps arrive in playback order, which is
what makes an interrupted transcript line exact rather than a guess.

## Time spent

Not recorded.
