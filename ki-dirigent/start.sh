#!/usr/bin/env sh
# KI-Dirigent starten (macOS / Linux)
cd "$(dirname "$0")" || exit 1
exec python3 -m dirigent "$@"
