#!/usr/bin/env bash
# Use project venv so openai SDK (Groq-compatible), PySide6, dotenv, locust match.
cd "$(dirname "$0")/.." || exit 1
exec ./.venv/bin/python attacker/locust_control_gui.py "$@"
