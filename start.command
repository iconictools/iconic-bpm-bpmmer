#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [[ ! -f "$SCRIPT_DIR/run.sh" ]]; then
  echo "[ERROR] run.sh not found in this folder." >&2
  exit 1
fi

if [[ ! -x "$SCRIPT_DIR/run.sh" ]]; then
  if ! chmod +x "$SCRIPT_DIR/run.sh"; then
    echo "[ERROR] Could not set execute permission on run.sh." >&2
    exit 1
  fi
fi

exec "$SCRIPT_DIR/run.sh"
