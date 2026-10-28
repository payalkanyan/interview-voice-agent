#!/usr/bin/env python3
"""Print p50 and p95 of user-stopped to first audio, plus the average time in each stage.

Usage: python scripts/latency_report.py logs/session-<time>
"""

import json
import sys
from pathlib import Path

# The stages a turn passes through, as consecutive pairs of latency.jsonl fields.
STAGES: tuple[tuple[str, str, str], ...] = (
    ("turn detection", "user_stopped_ms", "turn_end_ms"),
    ("stt final", "turn_end_ms", "stt_final_ms"),
    ("llm first token", "stt_final_ms", "llm_first_token_ms"),
    ("tts first audio", "llm_first_token_ms", "tts_first_audio_ms"),
)


def percentile(values: list[float], fraction: float) -> float:
    """Return the linearly interpolated percentile of ``values``.

    Matches the common "nearest rank with interpolation" definition, so a two-sample p95 sits
    close to the larger sample rather than exactly on it.
    """
    if not values:
        raise ValueError("no values")
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = fraction * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    weight = position - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def load_turns(session_dir: Path) -> list[dict]:
    """Read latency.jsonl from a session directory."""
    path = session_dir / "latency.jsonl"
    if not path.exists():
        raise SystemExit(f"No latency.jsonl in {session_dir}")
    turns = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not turns:
        raise SystemExit(f"{path} is empty")
    return turns


def total_latencies(turns: list[dict]) -> list[int]:
    """Milliseconds from the user going quiet to the coach's first audio, per complete turn."""
    return [
        turn["tts_first_audio_ms"] - turn["user_stopped_ms"]
        for turn in turns
        if turn.get("tts_first_audio_ms") is not None and turn.get("user_stopped_ms") is not None
    ]


def stage_averages(turns: list[dict]) -> list[tuple[str, float, int]]:
    """Average milliseconds spent in each stage, with the number of turns each average used."""
    out = []
    for label, start, end in STAGES:
        spans = [
            turn[end] - turn[start]
            for turn in turns
            if turn.get(start) is not None and turn.get(end) is not None
        ]
        if spans:
            out.append((label, sum(spans) / len(spans), len(spans)))
    return out


def report(session_dir: Path) -> str:
    """Build the printed report for one session."""
    turns = load_turns(session_dir)
    totals = total_latencies(turns)
    interrupted = sum(1 for turn in turns if turn.get("interrupted"))

    lines = [
        f"Session: {session_dir}",
        f"Turns: {len(turns)} ({interrupted} interrupted)",
        "",
        "User stopped speaking -> first coach audio",
    ]
    if totals:
        lines += [
            f"  p50  {percentile(totals, 0.50):7.0f} ms",
            f"  p95  {percentile(totals, 0.95):7.0f} ms",
            f"  min  {min(totals):7.0f} ms",
            f"  max  {max(totals):7.0f} ms",
        ]
    else:
        lines.append("  no complete turns")

    lines += ["", "Average time in each stage"]
    for label, average, count in stage_averages(turns):
        lines.append(f"  {label:<16} {average:7.0f} ms  (n={count})")

    lines += [
        "",
        "tts_first_audio_ms is when the audio frame was pushed to the transport, which is",
        "server send time rather than speaker playback. See README for the estimated gap.",
    ]
    return "\n".join(lines)


def main() -> None:
    """Print the report for the session directory given on the command line."""
    if len(sys.argv) != 2:
        raise SystemExit("usage: latency_report.py logs/session-<time>")
    print(report(Path(sys.argv[1])))


if __name__ == "__main__":
    main()
