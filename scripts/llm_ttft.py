#!/usr/bin/env python3
"""Measure time to first token for the candidate conversation models.

The conversation model sits on the critical path between the user going quiet and the coach
speaking, so the choice should come from a measurement rather than a hunch.

Usage: python scripts/llm_ttft.py [model ...]
"""

import asyncio
import os
import statistics
import sys
import time

from dotenv import load_dotenv

CANDIDATES = ("gemini-3.1-flash-lite", "gemini-3.5-flash-lite")
TRIALS = 5

SYSTEM = "You are a calm, friendly senior engineer running a mock interview. Short sentences only."
PROMPT = (
    "You just asked: 'Tell me about a project you're proud of.' They said: 'I built a payments "
    "service that retried failed charges.' Acknowledge it in one short sentence."
)


def api_key() -> str:
    """Read the Google API key, accepting either name the Gemini SDK recognises."""
    key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not key:
        raise SystemExit("Set GOOGLE_API_KEY (or GEMINI_API_KEY) to measure TTFT.")
    return key


async def time_to_first_token(client, model: str) -> float:
    """Return milliseconds from request to the first streamed text chunk."""
    from google.genai import types

    started = time.perf_counter()
    stream = await client.aio.models.generate_content_stream(
        model=model,
        contents=PROMPT,
        config=types.GenerateContentConfig(system_instruction=SYSTEM, temperature=0.6),
    )
    async for chunk in stream:
        if chunk.text:
            return (time.perf_counter() - started) * 1000
    raise RuntimeError(f"{model} streamed no text")


async def measure(models: tuple[str, ...]) -> None:
    """Measure every model over several trials and print the comparison."""
    from google import genai

    client = genai.Client(api_key=api_key())
    print(f"Time to first token, {TRIALS} trials each\n")
    for model in models:
        samples: list[float] = []
        for _ in range(TRIALS):
            try:
                samples.append(await time_to_first_token(client, model))
            except Exception as exc:
                print(f"{model:<28} failed: {exc}")
                break
        if samples:
            print(
                f"{model:<28} median {statistics.median(samples):6.0f} ms   "
                f"min {min(samples):6.0f} ms   max {max(samples):6.0f} ms"
            )


def main() -> None:
    """Measure the models named on the command line, or the default candidates."""
    load_dotenv()
    models = tuple(sys.argv[1:]) or CANDIDATES
    asyncio.run(measure(models))


if __name__ == "__main__":
    main()
