#!/bin/sh
# Install code only. Never starts services, downloads models or replays requests.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON=${PYTHON:-python3}
VENV=${WORKERS_AUDIO_VENV:-"$ROOT/.venvs/audioforge"}
"$PYTHON" -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ required"'
"$PYTHON" -m venv "$VENV"
"$VENV/bin/python" -m pip install -r "$ROOT/modules/audioforge/requirements.lock.txt"
"$VENV/bin/python" -m pip install --no-deps "$ROOT/modules/audioforge"
"$VENV/bin/python" -c 'import audioforge.file_workflow; print("Audio worker imports OK; no worker or model started")'
printf '\nInstalled: %s\nRead modules/audioforge/README.md before starting.\n' "$VENV"
