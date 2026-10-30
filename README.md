# Voice mock interview coach

A voice agent that runs a short mock interview in the browser. Four fixed questions in order, a
reaction to each answer, then spoken feedback. Pipecat, cascaded: separate STT, LLM and TTS.

## Run it

```bash
make setup                # venv with uv, then install
cp .env.example .env      # fill in 3 API keys and a Cartesia voice id
make run                  # open http://localhost:7860/client
```

`make test` (92 tests) · `make lint` · `make report SESSION=<dir>` · `make eval SESSION=<dir>`

Each session writes `transcript.txt`, `transcript.json` and `latency.jsonl` to
`logs/session-<timestamp>/`. Two recorded sessions are in [results/](results/). Smart Turn v3
needs no key — Pipecat bundles the ONNX weights.

## Architecture

```
browser mic -> SmallWebRTC -> Deepgram nova-3 -> Silero VAD + Smart Turn v3
            -> Gemini 3.1 Flash-Lite -> Cartesia sonic-3.6 -> browser speaker
```

| File | Responsibility |
|---|---|
| [bot.py](src/coach/bot.py) | Services and pipeline; every setting from `.env` |
| [flow.py](src/coach/flow.py) | Question graph, follow-up cap, feedback call |
| [prompts.py](src/coach/prompts.py) | Every word the coach can say that isn't generated |
| [latency_observer.py](src/coach/latency_observer.py) | Per-turn timings, as an observer |
| [transcript.py](src/coach/transcript.py) | Transcript, including interrupted lines |

**Order is structural.** Each question is a Flows node, the index lives in `flow_manager.state`,
and the LLM has one tool — `next_question` — so it cannot skip or reorder. Questions aren't
generated: each node speaks its own via a `tts_say` pre-action, so "word for word" holds by
construction.

**Reacting costs one LLM call.** A follow-up, nudge or re-ask is just the coach speaking; only
moving on costs a tool call. So a follow-up is invisible to the graph, and `FollowUpLimiter`
classifies each reply and writes the one-follow-up constraint into the context from code.

See also [docs/DESIGN.md](docs/DESIGN.md) (Pipecat 1.14 API, cited `file:line`),
[PROMPTS.md](PROMPTS.md) (prompts, and how they changed), [docs/DECISIONS.md](docs/DECISIONS.md).

## Providers, and why

| Layer | Choice | Why |
|---|---|---|
| STT | Deepgram `nova-3-general` | Streaming interim results, needed by the interruption rule; `keyterm` boosting |
| Turn detection | Silero VAD + `LocalSmartTurnAnalyzerV3` | Judges end-of-turn from the waveform, not the transcript. Local CPU, no key |
| LLM | `gemini-3.1-flash-lite` | The reaction task is small: classify one answer, write one sentence |
| Feedback | `gemini-3.1-flash-lite` | Was `gemini-3.8-flash`, which returned 503 on every attempt across two days |
| TTS | Cartesia `sonic-3.6` | Word timestamps, which make an interrupted transcript line exact, not estimated |
| Transport | SmallWebRTC | Local, browser only, no account |

`sonic-3` in the spec names a series, not an endpoint. `sonic-3.6` is what the installed service
documents.

## Turn-taking

All values from `.env`; defaults are Pipecat's own, cited in [docs/DESIGN.md](docs/DESIGN.md).

| Setting | Default | Mine | Why |
|---|---|---|---|
| `INTERRUPT_MIN_WORDS` | n/a | **2** | The spec's rule. The threshold applies *only while the coach speaks*, so two words interrupt but one still starts a normal turn — a cough cannot cut it off |
| `VAD_STOP_SECS` | 0.2 | 0.2 | Unchanged, and the recorded session shows this is too eager. See what's weak |
| `SMART_TURN_STOP_SECS` | 3.0 | 3.0 | Backstop after silence, not the normal path. It worked: one long answer held 3203 ms |
| `VAD_CONFIDENCE` / `START_SECS` / `MIN_VOLUME` | 0.7 / 0.2 / 0.6 | unchanged | Not shown to need changing |
| `SMART_TURN_PRE_SPEECH_MS` / `MAX_DURATION_SECS` | 500 / 8.0 | unchanged | Smart Turn takes at most 8 s of audio |

Only `INTERRUPT_MIN_WORDS` is a deliberate choice; the rest are defaults. Tuning them against a
recorded session is the one part of the spec's method I did not finish.

| Scenario | Observed in [results/second-call/](results/second-call/) |
|---|---|
| A, normal answer | Works |
| B, mid-answer pause | **Partly.** Held 3203 ms on a long answer; released a short trailing fragment in 566 ms |
| C, interruption | Works. 2 of 9 turns interrupted and recovered |

## Latency

`make report SESSION=results/second-call`, laid out more compactly here:

```
Turns: 9 (2 interrupted)

User stopped speaking -> first coach audio
  p50  2158 ms    p95  4140 ms    min  1444 ms    max  4894 ms

Average time in each stage (these add up to the total)
  turn detection    977 ms      llm first token  1201 ms      tts first audio  146 ms

Final transcript, relative to the end-of-turn decision:  mean -365 ms
```

p50 over two seconds is sluggish. The LLM is the largest slice and turn detection adds nearly a
second on top. The negative transcript offset means Deepgram finished before end-of-turn was
decided, so STT was never the bottleneck.

- **`user_stopped_ms` is when the user went quiet**, not when end of turn fired — separate frames,
  with the VAD's own `stop_secs` window subtracted back out. `turn_end_ms` is the decision, so the
  turn-detection and LLM waits stay separable.
- **`tts_first_audio_ms` is server send time, not playback.** Jitter buffer and playout add a gap
  this process cannot see, so time-to-sound is later. I did not estimate it.
- **STT is an offset, not a stage** — end-of-turn waits for whichever of the analyzer and the
  transcript is *later*, so as a stage it would read negative.

Logging is an observer, so it never sits in the pipeline's path; lines are buffered and written
once at the end.

`make eval SESSION=results/second-call`: all four questions asked in order, at most one follow-up
each, and the judge found every question answered and the one follow-up quoting the candidate.
One rule check fails — `feedback present` — for the first reason below.

## What's weak

- **A session can end with no feedback, and one did.** `FollowUpLimiter` treats any non-nudge,
  non-verbatim-re-ask reply as a follow-up, so a plain acknowledgement is miscounted — 3 of the 7
  model-generated replies in the second session. The backstop that forces the advance only fires
  once the follow-up is spent, so reaching the feedback depends on whether that question happened
  to have had a real follow-up first. It fired on question 3 and not on question 4.
- **A second, independent cause of the same symptom.** The default feedback model works now, but a
  `.env` written from the older `.env.example` still pins `gemini-3.8-flash`, which 503s every
  time and falls back to a fixed generic line.
- **Turn detection releases short trailing fragments.** "The hardest part was" ended the turn in
  566 ms and drew a nudge 982 ms later. On those turns `stt_final_ms` equals `turn_end_ms` within
  1 ms, so Smart Turn had already judged the fragment complete. The nudge is correct on bad input;
  `VAD_STOP_SECS=0.2` gives Smart Turn only 200 ms of silence to judge.
- **A question can play over the candidate.** `tts_say` fires on node entry regardless. The coach
  re-asks rather than accepting the fragment, which recovers but costs a turn.
- **Nudge and re-ask are matched by text.** A paraphrased nudge, or a re-ask not ending with the
  question verbatim, is charged to the budget — including one truncated by an interruption
  (`coach: Tell [INTERRUPTED]`).
- **STT garbling is unaddressed.** `DEEPGRAM_KEYTERMS` is wired up and empty. The session contains
  "I don't English color bank."; that garbling feeds the nudge problem directly.
- **The artifacts disagree on interruptions** (latency 2, transcript 1): a reply interrupted before
  any audio leaves a latency row but no transcript line. **Transcript line boundaries are also
  non-deterministic** — an acknowledgement and the next question merged into one line in the
  second session, separate in the first.

## Future improvements

1. **Make the feedback deterministic.** Classify an acknowledgement by the absence of a question
   mark and force the advance on any that didn't call `next_question`, whatever the budget. That
   test separates all 11 coach lines in the recorded session correctly, and the eval already uses
   it.
2. **Match a re-ask on a prefix**, so a truncated or reworded one isn't charged.
3. **Raise `VAD_STOP_SECS` to 0.5–0.8, re-measure**, and fill the table above from that session
   rather than from defaults.
4. **Fill `DEEPGRAM_KEYTERMS`** and re-check whether the nudges disappear with clean transcripts.
5. **Attack the 2158 ms p50** — turn detection and LLM are roughly half each. Measure from a
   machine closer to the API first; my `scripts/llm_ttft.py` numbers here had a spread wider than
   the gap between models.
6. **Reconcile the artifacts** so interruption counts agree and transcript lines are one utterance.

## AI tools

Built with Claude Code (Opus 5). The spec and the working agreement in `docs/CLAUDE.md` are mine;
the implementation was generated one commit per step against a reviewed plan.

The rule that mattered was in `docs/CLAUDE.md`: check the installed Pipecat source before using any
class, and say where you found it. Pipecat 1.x had moved nearly every API the spec implied. That
check became [docs/DESIGN.md](docs/DESIGN.md), and it surfaced the two details that make the
measurements honest: `VADUserStoppedSpeakingFrame` is distinct from `UserStoppedSpeakingFrame`, and
Cartesia's word timestamps arrive in playback order.

The second lesson: none of it survived contact with a real conversation. Every weakness above came
from two live calls, not from the suite, which was green throughout.

## Time spent

Not recorded.
