# CLAUDE.md

Voice mock interview coach built with Pipecat. Read `docs/SPEC.md` before doing anything.

## How we work

- Implement **one plan step at a time**. Stop after each step and show me the diff. Do not start the next step until I say so.
- If something is unclear or the spec doesn't cover it, **ask me** instead of guessing.
- Before using a Pipecat class or frame, check the installed source under `.venv/lib/python3.12/site-packages/pipecat/` and tell me where you found it. Do not rely on memory for the Pipecat API.
- Do not add providers, fallbacks, config options or features I didn't ask for.
- Do not edit `docs/SPEC.md` or files in `results/` unless I ask.

## Code style

- Keep it simple and readable.
- Prefer plain functions and small files over abstractions. No base classes or factories for one implementation.
- Type hints on function signatures. Short docstrings only where the why isn't obvious.
- No commented-out code, no leftover debug prints. Use `loguru` for logging.
- Config comes from `.env`. Every new key or setting goes in `.env.example` in the same change.

## Commits

- One logical change per commit.
- Message: imperative, lowercase, under 50 characters, no trailing period. Examples: `add voice pipeline`, `tune smart turn timeout`, `log turn end time`.
- No "update", "fix stuff", "wip" or "final".
- Run `make test` and `make lint` before every commit. Never commit `.env`, `logs/` or audio files.
- Do not commit for me unless I ask. Suggest the message instead.

## Commands

- `make setup`: create venv with uv and install
- `make run`: start the bot, open http://localhost:7860/client
- `make test`: run pytest
- `make lint`: ruff check and format
- `make report SESSION=logs/session-<time>`: latency report
- `make eval SESSION=logs/session-<time>`: transcript eval

## Layout

```
src/coach/       bot.py, flow.py, prompts.py, latency_observer.py, transcript.py
scripts/         latency_report.py, eval_transcript.py, llm_ttft.py
tests/
docs/            SPEC.md, PLAN.md, DESIGN.md, DECISIONS.md
results/         the one recorded session (latency.jsonl, transcript)
```

## Stack

- Python 3.12, Pipecat (pinned in `pyproject.toml`)
- STT: Deepgram nova-3 streaming
- Turn detection: Silero VAD + Smart Turn v3
- LLM: Gemini 3.1 Flash-Lite for conversation, a stronger model for final feedback(may change based on performance and latency)
- TTS: Cartesia sonic-3
- Transport: SmallWebRTC

## Docs

- Write for a busy reviewer: plain sentences, short sections, every table followed by one sentence saying what it means.
- Numbers in docs must come from a real session log. Never invent or estimate a number without saying so.
- When we change a provider, setting or approach, add a dated entry to `docs/DECISIONS.md`: what changed, why, and what measurement showed it.