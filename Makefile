.PHONY: setup run test lint report eval

VENV := .venv
PY := $(VENV)/bin/python

setup:
	uv venv --python 3.12
	uv pip install -e ".[dev]"

run:
	$(PY) -m coach.bot

test:
	$(VENV)/bin/pytest

lint:
	$(VENV)/bin/ruff check .
	$(VENV)/bin/ruff format .

report:
	@test -n "$(SESSION)" || { echo "usage: make report SESSION=logs/session-<time>"; exit 1; }
	$(PY) scripts/latency_report.py $(SESSION)

eval:
	@test -n "$(SESSION)" || { echo "usage: make eval SESSION=logs/session-<time>"; exit 1; }
	$(PY) scripts/eval_transcript.py $(SESSION)
