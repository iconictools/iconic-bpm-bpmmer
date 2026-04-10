#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
#  iconic-bpm-bpmmer  –  auto-build & run script
#
#  Usage:
#    ./run.sh              # install deps (if needed) and start server
#    ./run.sh --install    # force reinstall all dependencies
#    ./run.sh --port 8080  # choose a custom port (default: 5000)
#    ./run.sh --help       # show this message
# ─────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT=5000
FORCE_INSTALL=0

# ── Argument parsing ─────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --install)    FORCE_INSTALL=1 ; shift ;;
    --port)       PORT="$2"        ; shift 2 ;;
    --help|-h)
      sed -n '/^# Usage/,/^# ──/{ /^# ──/d; s/^# \{0,2\}//; p }' "$0"
      exit 0
      ;;
    *) echo "Unknown option: $1" >&2 ; exit 1 ;;
  esac
done

cd "$SCRIPT_DIR"

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║        iconic-bpm-bpmmer  —  Ultimate BPM Finder        ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# ── Python check ─────────────────────────────────────────────────
PYTHON=""
for cmd in python3 python; do
  if command -v "$cmd" &>/dev/null; then
    VER=$("$cmd" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
    MAJOR=${VER%%.*}
    MINOR=${VER##*.}
    if [[ "$MAJOR" -ge 3 && "$MINOR" -ge 9 ]]; then
      PYTHON="$cmd"
      break
    fi
  fi
done

if [[ -z "$PYTHON" ]]; then
  echo "✗  Python 3.9+ not found. Please install Python first." >&2
  exit 1
fi
echo "✓  Python: $($PYTHON --version)"

# ── ffmpeg check (optional but recommended for MP3 export) ───────
if command -v ffmpeg &>/dev/null; then
  echo "✓  ffmpeg: $(ffmpeg -version 2>&1 | head -1 | awk '{print $3}')"
else
  echo "⚠  ffmpeg not found – MP3 export will be unavailable."
  echo "   Install via: sudo apt install ffmpeg  (Ubuntu/Debian)"
  echo "               brew install ffmpeg        (macOS)"
fi

# ── Virtual environment ───────────────────────────────────────────
VENV_DIR="$SCRIPT_DIR/.venv"

if [[ "$FORCE_INSTALL" -eq 1 ]] && [[ -d "$VENV_DIR" ]]; then
  echo "→  Removing existing virtual environment …"
  rm -rf "$VENV_DIR"
fi

if [[ ! -d "$VENV_DIR" ]]; then
  echo "→  Creating virtual environment …"
  "$PYTHON" -m venv "$VENV_DIR"
fi

# Use the venv python/pip from now on
PY="$VENV_DIR/bin/python"
PIP="$VENV_DIR/bin/pip"

# ── Dependency installation ───────────────────────────────────────
STAMP="$VENV_DIR/.deps_installed"
REQ="$SCRIPT_DIR/requirements.txt"

needs_install() {
  [[ ! -f "$STAMP" ]] && return 0
  [[ "$REQ" -nt "$STAMP" ]] && return 0
  return 1
}

if [[ "$FORCE_INSTALL" -eq 1 ]] || needs_install; then
  echo "→  Installing Python dependencies …"
  "$PIP" install --quiet --upgrade pip
  "$PIP" install --quiet -r "$REQ"
  touch "$STAMP"
  echo "✓  Dependencies installed"
else
  echo "✓  Dependencies up-to-date (use --install to force reinstall)"
fi

# ── Ensure upload/output directories exist ────────────────────────
mkdir -p "$SCRIPT_DIR/uploads" "$SCRIPT_DIR/outputs"

# ── Launch server ─────────────────────────────────────────────────
echo ""
echo "→  Starting server on http://localhost:${PORT}"
echo "   Press Ctrl+C to stop."
echo ""

exec "$PY" "$SCRIPT_DIR/app.py" --port "$PORT"
