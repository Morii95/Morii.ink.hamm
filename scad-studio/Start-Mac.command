#!/bin/bash
# SCAD Studio starten (macOS) – Doppelklick genügt.
cd "$(dirname "$0")"
if command -v python3 >/dev/null 2>&1; then
  python3 start.py "$@"
else
  echo "Python 3 wurde nicht gefunden. Bitte von https://www.python.org/downloads/ installieren."
  read -r -p "Enter zum Schließen …"
fi
