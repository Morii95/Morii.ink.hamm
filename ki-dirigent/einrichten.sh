#!/usr/bin/env sh
# KI-Dirigent einrichten (macOS / Linux)
cd "$(dirname "$0")" || exit 1
exec python3 -m dirigent.einrichten "$@"
