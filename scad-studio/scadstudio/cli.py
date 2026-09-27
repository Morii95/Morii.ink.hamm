"""Kommandozeile – z. B. für Claude Code oder Skripte.

    python -m scadstudio serve                      Oberfläche starten
    python -m scadstudio ai "Handyhalter" -i foto.png
    python -m scadstudio render lampe.scad          rendern, Teile exportieren, prüfen
    python -m scadstudio check teil.stl             STL prüfen (Netz, Maße, Wände, Druckbett)
    python -m scadstudio image logo.png --mode keychain --size 50
    python -m scadstudio printers                   Druckerprofile anzeigen

Alle Befehle legen ein Projekt im Projektordner an, das auch in der
Oberfläche erscheint. Ausgabe auf Deutsch, Ergebnis optional als JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import APP_NAME, __version__, openscad
from .config import Settings
from .jobs import Job, JobError
from .printers import PRINTERS, printer_profile

COLORS = {"error": "\033[31m", "warning": "\033[33m", "success": "\033[32m"}


class ConsoleJob(Job):
    """Auftrag, der sein Protokoll direkt ausgibt."""

    def __init__(self, title: str, quiet: bool = False):
        super().__init__("cli", title)
        self.quiet = quiet

    def info(self, text: str, level: str = "info", **extra: Any) -> None:
        super().info(text, level, **extra)
        if self.quiet and level not in ("error", "warning"):
            return
        color = COLORS.get(level, "") if sys.stderr.isatty() else ""
        reset = "\033[0m" if color else ""
        print(f"{color}• {text}{reset}", file=sys.stderr)


def _store(settings: Settings):
    from .projects import ProjectStore
    return ProjectStore(settings.projects_dir())


def _report(project, as_json: bool) -> int:
    meta = project.meta
    problems = meta.get("problems") or []
    render = meta.get("render") or {}
    if as_json:
        print(json.dumps({"project": project.id, "folder": str(project.path), "render": render,
                          "check": meta.get("check"), "parts": meta.get("parts"),
                          "problems": problems}, ensure_ascii=False, indent=2))
    else:
        print()
        print(f"Projekt: {project.path}")
        check = meta.get("check") or {}
        if check.get("size"):
            print("Gesamtmaße: {:.1f} × {:.1f} × {:.1f} mm".format(*check["size"]))
        for part in meta.get("parts") or []:
            if part.get("unused"):
                print(f"  Teil {part['id']:<20} (in dieser Variante nicht verwendet)")
                continue
            pc = part.get("check") or {}
            size = "{:.1f} × {:.1f} × {:.1f} mm".format(*pc["size"]) if pc.get("size") else "Fehler"
            bed = pc.get("bed") or {}
            fit = "passt" if bed.get("fits") else ("passt gedreht" if bed.get("fits_rotated") else "ZU GROSS")
            print(f"  Teil {part['id']:<20} {size:<28} {fit:<14} {part.get('stl', '')}")
        fit = meta.get("collisions")
        if fit:
            print(f"Passungen: {len(fit['pairs'])} Teilepaare geprüft, {len(fit['collisions'])} Kollision(en)")
            for pair in fit["pairs"]:
                state = "frei" if pair.get("ok") else ("KOLLISION %.1f mm³" % pair.get("volume_mm3", 0)
                                                        if pair.get("ok") is False else pair.get("error", "?"))
                if pair.get("region"):
                    from .pipeline import region_text
                    state += f" bei {region_text(pair['region'])}"
                print(f"  {pair['label']:<40} {state}")
        if problems:
            print(f"\n{len(problems)} Problem(e):")
            for p in problems:
                print("  -", p)
        elif render.get("ok"):
            print("\nPrüfung bestanden.")
    return 0 if render.get("ok") and not problems else 1


def cmd_serve(args: argparse.Namespace) -> int:
    import threading
    import webbrowser

    from .server import Studio, serve

    studio = Studio()
    httpd = serve(port=args.port, studio=studio)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"{APP_NAME} läuft: {url}  (Strg+C beendet)")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


def cmd_ai(args: argparse.Namespace) -> int:
    from . import pipeline

    settings = Settings()
    if args.provider:
        settings._data["provider"] = args.provider  # nur für diesen Aufruf
    project = _store(settings).create(args.title or args.prompt[:50] or "KI-Modell", "ai")
    names = [project.add_input(Path(p).read_bytes(), Path(p).suffix) for p in args.image or []]
    job = ConsoleJob("KI-Konstruktion", args.quiet)
    try:
        pipeline.ai_generate(job, project, settings, args.prompt, names)
    except JobError as exc:
        job.info(str(exc), "error")
        return 2
    return _report(project, args.json)


def cmd_render(args: argparse.Namespace) -> int:
    from . import pipeline

    settings = Settings()
    source = Path(args.file)
    code = source.read_text(encoding="utf-8")
    project = _store(settings).create(args.title or source.stem, "code")
    project.write_code(code, f"Importiert aus {source.name}")
    job = ConsoleJob("Rendern", args.quiet)
    try:
        pipeline.render_and_check(job, project, settings, export_parts=not args.no_parts)
        if args.fit:
            fit = pipeline.collision_check(job, project, settings)["collisions"]
            problems = list(project.meta.get("problems") or []) + pipeline.collision_problems(fit)
            project.update(problems=problems)
    except JobError as exc:
        job.info(str(exc), "error")
        return 2
    return _report(project, args.json)


def cmd_check(args: argparse.Namespace) -> int:
    from . import meshcheck

    settings = Settings()
    printer = printer_profile(settings)
    result = meshcheck.analyze(args.file, bed=tuple(printer["bed"]), min_wall=printer["min_wall"],
                               recommended_wall=printer["rec_wall"])
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(meshcheck.summary_text(result))
    return 0 if result.get("ok") else 1


def cmd_image(args: argparse.Namespace) -> int:
    from . import pipeline

    settings = Settings()
    source = Path(args.file)
    project = _store(settings).create(args.title or source.stem, "image")
    name = project.add_input(source.read_bytes(), source.suffix)
    params: dict[str, Any] = {}
    for item in args.param or []:
        key, _, value = item.partition("=")
        params[key.strip()] = _parse_value(value.strip())
    for key in ("size", "height", "width", "depth", "base", "frame", "threshold", "resolution", "thicken"):
        value = getattr(args, key, None)
        if value is not None:
            params[key] = value
    if args.invert:
        params["invert"] = True
    job = ConsoleJob("Bild → 3D", args.quiet)
    try:
        pipeline.image_to_model(job, project, settings, name, args.mode, params)
    except JobError as exc:
        job.info(str(exc), "error")
        return 2
    return _report(project, args.json)


def cmd_printers(args: argparse.Namespace) -> int:
    settings = Settings()
    active = printer_profile(settings)["id"]
    for pid, (name, x, y, z) in PRINTERS.items():
        mark = "*" if pid == active else " "
        print(f"{mark} {pid:<16} {name:<32} {x:g} × {y:g} × {z:g} mm")
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    settings = Settings()
    info = openscad.detect(settings.get("openscad_path") or "")
    printer = printer_profile(settings)
    print(f"{APP_NAME} {__version__}")
    print("OpenSCAD:", f"{info.version} – {info.path}" + (" (Manifold)" if info.manifold_flag else "")
          if info.found else info.error)
    print("KI-Anbieter:", settings.get("provider"))
    print("Drucker:", printer["name"], "×".join(f"{v:g}" for v in printer["bed"]), "mm")
    print("Projekte:", settings.projects_dir())
    return 0


def _parse_value(value: str) -> Any:
    low = value.lower()
    if low in ("true", "ja", "yes"):
        return True
    if low in ("false", "nein", "no"):
        return False
    try:
        return float(value) if "." in value else int(value)
    except ValueError:
        return value


def main(argv: list[str] | None = None) -> int:
    # Windows-Konsolen/Pipes nutzen sonst cp1252 → Absturz bei „↔“, „×“ usw.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(prog="python -m scadstudio",
                                     description=f"{APP_NAME} – KI-3D-Studio für OpenSCAD")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("serve", help="Oberfläche im Browser starten")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(func=cmd_serve)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--title", help="Projektname")
        p.add_argument("--json", action="store_true", help="Ergebnis als JSON ausgeben")
        p.add_argument("--quiet", "-q", action="store_true", help="nur Warnungen/Fehler ausgeben")

    p = sub.add_parser("ai", help="Modell per KI aus Text/Bildern erzeugen")
    p.add_argument("prompt", nargs="?", default="")
    p.add_argument("-i", "--image", action="append", help="Referenzbild (mehrfach möglich)")
    p.add_argument("--provider", choices=["gemini", "anthropic", "openai", "claude_cli"])
    common(p)
    p.set_defaults(func=cmd_ai)

    p = sub.add_parser("render", help=".scad-Datei rendern, Teile exportieren und prüfen")
    p.add_argument("file")
    p.add_argument("--no-parts", action="store_true", help="Einzelteile nicht exportieren")
    p.add_argument("--fit", action="store_true", help="Passungs-/Kollisionsprüfung im Zusammenbau")
    common(p)
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("check", help="STL-Datei prüfen")
    p.add_argument("file")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("image", help="Bild in ein 3D-Modell umwandeln")
    p.add_argument("file")
    p.add_argument("--mode", default="relief",
                   choices=["relief", "lithophane", "extrude", "plate", "keychain", "stamp",
                            "cookie_cutter", "stencil"])
    for key in ("size", "height", "width", "depth", "base", "frame", "thicken"):
        p.add_argument(f"--{key}", type=float)
    p.add_argument("--threshold", type=int)
    p.add_argument("--resolution", type=int)
    p.add_argument("--invert", action="store_true")
    p.add_argument("--param", "-p", action="append", help="weitere Parameter, z. B. -p ring_outer=12")
    common(p)
    p.set_defaults(func=cmd_image)

    p = sub.add_parser("printers", help="Druckerprofile anzeigen")
    p.set_defaults(func=cmd_printers)
    p = sub.add_parser("info", help="Einrichtung anzeigen (OpenSCAD, KI, Drucker)")
    p.set_defaults(func=cmd_info)

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
