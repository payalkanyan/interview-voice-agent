# Prompts

Every prompt is in [src/coach/prompts.py](src/coach/prompts.py) so there is one source of truth.
This document quotes them as used, then explains the decision logic around them.

## Persona

Sent once as the system instruction on the first node. In Pipecat Flows a `role_message` persists
across node transitions, so it is set on the greeting node and never repeated.

```
You are a calm, friendly senior engineer running a short mock interview.

You are encouraging but honest. You use short sentences. You never lecture, never give advice
unless asked, and never list things.

Your words are spoken aloud, so write only plain spoken English: no markdown, no bullet points,
no emoji, no special characters, no stage directions.
```

### Voice shaping

Three things in that prompt exist only because the output is spoken:

- **"no markdown, no bullet points, no emoji, no special characters"** — a TTS engine reads
  asterisks and em-dashes aloud or stumbles on them.
- **"never lists things"** — a spoken list has no visual structure, so it arrives as a run-on
  sentence. This also keeps replies short enough not to invite interruption.
- **"short sentences"** — the TTS streams per sentence, so shorter sentences mean the first audio
  starts sooner.

## Fixed lines

These are spoken verbatim by code, never generated.

| Line | Text |
|---|---|
| Greeting | `Hi, thanks for making the time. This will be short, just four questions.` |
| Nudge | `Take your time. Even a small project works.` |
| Bridge | `Let me gather my thoughts for a second.` |
| Goodbye | `That's everything. Thanks for talking it through with me, and good luck.` |

The four interview questions are also spoken verbatim, by a `tts_say` pre-action on each node:

1. `Tell me about a project you're proud of.`
2. `What was the hardest technical problem in it?`
3. `How did you know it was working well?`
4. `What would you do differently now?`

## Reaction prompt

This is the task message on every question node. `{question}` is the question that node asked and
`{nudge}` is the nudge line above.

```
You just asked the candidate this question: "{question}"

They have now replied. Do exactly one of these three things, and nothing else.

MOVE ON. The answer is good enough. Acknowledge it warmly in at most one short sentence, then
call next_question.

FOLLOW UP. The answer is vague, or it skips over the interesting part. Ask one short follow-up
question. It must refer to something they actually said, in their own words. Do not call
next_question.

NUDGE. They are stuck, said nothing, or asked you for help. Say exactly this and nothing more:
"{nudge}" Do not call next_question.

Never ask one of the other interview questions yourself. next_question does that for you.
Never repeat a question you have already asked.
```

### Decision logic

The three branches are a prompt decision; the consequences are code.

| Branch | What the model does | What code does |
|---|---|---|
| Move on | Calls `next_question` | The flow graph transitions to the next question node, which speaks it verbatim |
| Follow up | Speaks, no tool call | `FollowUpLimiter` marks the budget spent and injects the constraint below |
| Nudge | Speaks the nudge line exactly | Recognised by text match; the budget is untouched |

The model has exactly one tool, so it cannot skip, reorder or invent a question. Advancing costs
a tool call plus a second LLM turn for the new node; following up and nudging cost one turn, which
is the common case.

Once a follow-up is spent, code writes this into the context as a developer message:

```
You have now used your one follow-up for this question. Do not ask another
follow-up about it. On your next turn, either acknowledge their answer in one short sentence and
call next_question, or, if they are stuck, say the nudge line.
```

And on advancing, code retires it:

```
That question is done. You may ask one follow-up about the new question.
```

## Feedback prompt

A separate call to a stronger model (`gemini-3.8-flash`), started the moment the fourth answer is
accepted so that the bridge line is spoken *while* it runs rather than before it. System
instruction:

```
You are a calm, friendly senior engineer who has just finished running a
mock interview. You are encouraging but honest.

Write two or three sentences of spoken feedback. Name one strength and one thing to improve.
Tie each one to something specific the candidate actually said, quoting their own words where you
can. Do not invent anything they did not say.

Speak to them directly as "you". Write only plain spoken English: no markdown, no bullet points,
no emoji, no special characters. Two or three sentences, nothing more.
```

The user message is the transcript, rendered from the LLM context as `Coach:` / `Candidate:`
lines, followed by `Give your two to three sentences of feedback now.`

"Do not invent anything they did not say" is there because the spec requires feedback tied to
something specific, and a model asked to be encouraging will otherwise praise qualities the
transcript does not show.

If the call fails, a fixed fallback line is spoken instead, so the session never ends in silence.
That line is generic, which is a known weakness rather than a design choice.

## Iterations

### Asking the questions: generated, then spoken by code

The first design put the question in the node's task message and asked the model to ask it
verbatim, which is the obvious reading of "it asks these questions, word for word".

That is not reliable, and it also conflicts with the same prompt having to handle the *reaction*
to the answer. One task message would have had to mean "ask this if you haven't yet, otherwise
react", and the model decides which.

Now each node speaks its question through a `tts_say` pre-action with `respond_immediately=False`.
The question is a fixed string, so "word for word" is guaranteed by construction, and the task
message has exactly one job: react to the answer. A test asserts the exact wording.

### The follow-up constraint: injected, then also retired

The first version of the cap injected the developer message above and left it there.

A test walking two questions showed the bug: the message says "this question", but it stays in
the context for the rest of the interview. By question three the model is carrying an
instruction not to follow up that was written about question one, so later questions silently
lose their follow-up.

The fix is the `FOLLOW_UP_RESET` line, written on every advance where the budget had been spent.
Extracting the reset into its own method is also what made it testable;
`test_advancing_restores_the_budget_for_the_next_question` now asserts that question two really
can have its own follow-up.

## Known failures

- **A paraphrased nudge spends the follow-up.** The nudge is recognised by comparing the reply
  against the nudge line. If the model says "Take your time, even something small is fine"
  instead of the exact words, the limiter reads it as a follow-up.
- **Nothing physically prevents a second follow-up.** Code owns the state and injects the
  constraint, but the model could still ask one. It is logged as a warning and the eval's rule
  check catches it in the transcript.
- **An acknowledgement phrased as a question miscounts in the eval.** "That sounds painful,
  right?" would be counted as a follow-up by the rule check. The LLM judge covers this.
- **The reaction prompt cannot see how long the answer was.** A one-word answer and a
  two-minute answer reach the model as text with no duration, so "vague" is judged on content
  alone.
- **None of this has been tested against a live conversation.** Every claim above about the
  prompts comes from the code and its tests, not from a recorded session.
