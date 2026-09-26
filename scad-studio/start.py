#!/usr/bin/env python3
"""SCAD Studio starten.

    python start.py              Oberfläche im Browser öffnen
    python start.py --port 9000  anderen Port verwenden
    python start.py --no-browser nur den Server starten

Weitere Befehle für das Terminal (z. B. für Claude Code): python -m scadstudio --help
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent


def ensure_dependencies() -> None:
    """Installiert fehlende Pakete aus requirements.txt beim ersten Start."""
    missing = []
    try:
        import PIL  # noqa: F401
    except ImportError:
        missing.append("Pillow")
    if sys.version_info >= (3, 10):
        try:
            import anthropic  # noqa: F401
        except ImportError:
            missing.append("anthropic")
    if not missing:
        return
    print(f"Installiere fehlende Pakete: {', '.join(missing)} …")
    cmd = [sys.executable, "-m", "pip", "install", "--user", "-r", str(HERE / "requirements.txt")]
    if sys.prefix != sys.base_prefix:  # in einer virtuellen Umgebung ohne --user
        cmd.remove("--user")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print("\nAutomatische Installation fehlgeschlagen. Bitte manuell ausführen:\n"
              f"  {sys.executable} -m pip install -r requirements.txt\n")
        try:
            import PIL  # noqa: F401
        except ImportError:
            sys.exit(1)


def main() -> None:
    if sys.version_info < (3, 9):
        sys.exit("SCAD Studio benötigt Python 3.9 oder neuer (empfohlen: 3.11+).")
    parser = argparse.ArgumentParser(description="SCAD Studio – KI-3D-Studio für OpenSCAD")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="Browser nicht automatisch öffnen")
    args = parser.parse_args()

    ensure_dependencies()
    sys.path.insert(0, str(HERE))
    from scadstudio import openscad
    from scadstudio.server import Studio, serve

    studio = Studio()
    httpd = serve(port=args.port, studio=studio)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    info = openscad.detect(studio.settings.get("openscad_path") or "")
    print("=" * 60)
    print("  SCAD Studio läuft:", url)
    print("  OpenSCAD:", f"{info.version} ({info.path})" if info.found else "NICHT GEFUNDEN – Pfad in den Einstellungen angeben")
    print("  Projekte:", studio.settings.projects_dir())
    print("  Beenden mit Strg+C")
    print("=" * 60)
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nSCAD Studio beendet.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
