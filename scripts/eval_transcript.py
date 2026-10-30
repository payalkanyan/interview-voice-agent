#!/usr/bin/env python3
"""Check one session transcript against the rules in docs/SPEC.md, then ask a judge.

The rule checks are deterministic and always run. The LLM judge covers the parts that need
reading comprehension, and is skipped when no API key is set.

Usage: python scripts/eval_transcript.py logs/session-<time>
"""

import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from coach import prompts  # noqa: E402

JUDGE_MODEL_DEFAULT = "gemini-3.1-flash-lite"

JUDGE_SYSTEM = """You are grading one mock-interview transcript. Be strict and literal: judge
only what the transcript shows, and quote it when you justify a verdict.

Reply with JSON only, matching this shape exactly:

{
  "questions_answered": [
    {"question": "<the question>", "answered": true, "why": "<one sentence>"}
  ],
  "follow_ups": [
    {"follow_up": "<what the coach asked>", "references_user_words": true, "why": "<one sentence>"}
  ],
  "feedback_specific": {"specific": true, "why": "<one sentence>"}
}

For follow_ups, include every coach question that was not one of the four fixed interview
questions. If there were none, use an empty list."""

JUDGE_USER = """The four fixed interview questions were:

{questions}

Transcript:

{transcript}

Grade it now, as JSON only."""


def normalize(text: str) -> str:
    """Reduce a line to comparable words."""
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", text.lower()).split())


def count_sentences(text: str) -> int:
    """Count sentences by terminal punctuation."""
    return len([part for part in re.split(r"[.!?]+(?:\s|$)", text.strip()) if part.strip()])


def load_lines(session_dir: Path) -> list[dict]:
    """Read transcript.json from a session directory."""
    path = session_dir / "transcript.json"
    if not path.exists():
        raise SystemExit(f"No transcript.json in {session_dir}")
    return json.loads(path.read_text())


def question_positions(lines: list[dict]) -> list[int | None]:
    """Find the transcript index where each fixed question was asked, or None if never."""
    positions: list[int | None] = []
    for question in prompts.QUESTIONS:
        wanted = normalize(question)
        found = None
        for index, line in enumerate(lines):
            if line["speaker"] == "coach" and wanted in normalize(line["text"]):
                found = index
                break
        positions.append(found)
    return positions


def find_feedback(lines: list[dict]) -> str | None:
    """Find the spoken feedback: the first coach line after the bridge line."""
    bridge = normalize(prompts.BRIDGE)
    goodbye = normalize(prompts.GOODBYE)
    seen_bridge = False
    for line in lines:
        if line["speaker"] != "coach":
            continue
        text = normalize(line["text"])
        if bridge in text:
            seen_bridge = True
            continue
        if seen_bridge and text and goodbye not in text:
            return line["text"]
    return None


def count_follow_ups(lines: list[dict], positions: list[int | None]) -> list[int]:
    """Count coach questions asked between each fixed question and the next.

    A follow-up is heuristically a coach line containing a question mark that is neither one of
    the fixed questions nor the nudge. Whether it genuinely follows up is the judge's job.
    """
    nudge = normalize(prompts.NUDGE)
    fixed = {normalize(question) for question in prompts.QUESTIONS}
    counts = []
    for index, start in enumerate(positions):
        if start is None:
            counts.append(0)
            continue
        following = [p for p in positions[index + 1 :] if p is not None]
        end = following[0] if following else len(lines)
        total = 0
        for line in lines[start + 1 : end]:
            if line["speaker"] != "coach" or "?" not in line["text"]:
                continue
            text = normalize(line["text"])
            if text == nudge or any(question in text for question in fixed):
                continue
            total += 1
        counts.append(total)
    return counts


def rule_checks(lines: list[dict]) -> tuple[list[tuple[bool, str]], str | None]:
    """Run every deterministic check, returning (passed, description) pairs."""
    positions = question_positions(lines)
    asked = [p for p in positions if p is not None]
    results: list[tuple[bool, str]] = []

    results.append(
        (
            len(asked) == len(prompts.QUESTIONS),
            f"all {len(prompts.QUESTIONS)} questions asked ({len(asked)} found)",
        )
    )
    results.append(
        (asked == sorted(asked) and len(asked) == len(prompts.QUESTIONS), "questions in order")
    )

    follow_ups = count_follow_ups(lines, positions)
    for index, count in enumerate(follow_ups):
        results.append((count <= 1, f"question {index + 1}: at most one follow-up ({count})"))

    feedback = find_feedback(lines)
    results.append((feedback is not None, "feedback present"))
    if feedback is not None:
        sentences = count_sentences(feedback)
        results.append((2 <= sentences <= 3, f"feedback is 2 to 3 sentences ({sentences})"))

    return results, feedback


def judge(lines: list[dict]) -> dict | None:
    """Ask a stronger model the questions that need reading comprehension."""
    key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not key:
        return None

    from google import genai
    from google.genai import types

    transcript = "\n".join(
        f"{line['speaker']}: {line['text']}" + (" [INTERRUPTED]" if line["interrupted"] else "")
        for line in lines
    )
    questions = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(prompts.QUESTIONS))

    model = os.getenv("EVAL_JUDGE_MODEL") or JUDGE_MODEL_DEFAULT
    client = genai.Client(api_key=key)
    try:
        response = client.models.generate_content(
            model=model,
            contents=JUDGE_USER.format(questions=questions, transcript=transcript),
            config=types.GenerateContentConfig(
                system_instruction=JUDGE_SYSTEM,
                temperature=0.0,
                response_mime_type="application/json",
            ),
        )
    except Exception as exc:
        # The rule checks are the deterministic part of the eval and must still be reported.
        print(f"Judge call to {model} failed: {exc}")
        return None

    try:
        return json.loads(response.text or "")
    except json.JSONDecodeError:
        print(f"Judge did not return JSON:\n{response.text}")
        return None


def print_report(session_dir: Path) -> int:
    """Print the evaluation and return the number of failed rule checks."""
    lines = load_lines(session_dir)
    results, feedback = rule_checks(lines)

    print(f"Session: {session_dir}")
    print(f"Lines: {len(lines)}\n")
    print("Rule checks")
    failures = 0
    for passed, description in results:
        print(f"  {'PASS' if passed else 'FAIL'}  {description}")
        failures += 0 if passed else 1

    if feedback:
        print(f'\nFeedback spoken:\n  "{feedback}"')

    verdict = judge(lines)
    if verdict is None:
        print("\nLLM judge skipped (no GOOGLE_API_KEY or GEMINI_API_KEY set).")
    else:
        print("\nLLM judge")
        for item in verdict.get("questions_answered", []):
            mark = "yes" if item.get("answered") else "NO "
            print(f"  answered [{mark}] {item.get('question', '')}")
            print(f"            {item.get('why', '')}")
        follow_ups = verdict.get("follow_ups", [])
        if not follow_ups:
            print("  no follow-ups were asked")
        for item in follow_ups:
            mark = "yes" if item.get("references_user_words") else "NO "
            print(f"  quotes user [{mark}] {item.get('follow_up', '')}")
            print(f"              {item.get('why', '')}")
        specific = verdict.get("feedback_specific", {})
        mark = "yes" if specific.get("specific") else "NO "
        print(f"  feedback specific [{mark}] {specific.get('why', '')}")

    print(f"\n{failures} rule check(s) failed." if failures else "\nAll rule checks passed.")
    return failures


def main() -> None:
    """Evaluate the session directory given on the command line."""
    load_dotenv()
    if len(sys.argv) != 2:
        raise SystemExit("usage: eval_transcript.py logs/session-<time>")
    raise SystemExit(1 if print_report(Path(sys.argv[1])) else 0)


if __name__ == "__main__":
    main()
