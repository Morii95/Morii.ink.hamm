"""Anbindung an die lokal installierte OpenSCAD-Version.

Sucht das Programm, rendert .scad-Dateien zu STL/PNG und wertet die
Konsolenausgabe (ERROR/WARNING/ECHO) für die Oberfläche und die
KI-Reparaturschleife aus.
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

# Warnungen, die fast immer einen echten Fehler im Code bedeuten.
# Die KI-Reparatur behandelt sie wie Fehler.
SERIOUS_WARNING_PATTERNS = [
    r"unknown (variable|function|module)",
    r"ignoring unknown",
    r"undefined operation",
    r"too many unnamed arguments",
    r"was assigned on line",
    r"unable to convert",
    r"not a valid 2-manifold",
    r"mesh is not closed",
    r"polyset has nonplanar faces",
    r"scaling a 3d object with 0",
    r"bad range parameter",
    r"invalid (type|parameter)",
    r"can't open (include|library|import)",
    r"failed to open file",
]

WINDOWS_CANDIDATES = [
    r"{pf}\OpenSCAD\openscad.com",
    r"{pf}\OpenSCAD\openscad.exe",
    r"{pf}\OpenSCAD (Nightly)\openscad.com",
    r"{pf}\OpenSCAD (Nightly)\openscad.exe",
    r"{pf}\OpenSCAD*\openscad.com",
    r"{pf}\OpenSCAD*\openscad.exe",
]

MAC_CANDIDATES = [
    "/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD",
    "/Applications/OpenSCAD*.app/Contents/MacOS/OpenSCAD",
    "~/Applications/OpenSCAD*.app/Contents/MacOS/OpenSCAD",
]

LINUX_NAMES = ["openscad", "openscad-nightly", "OpenSCAD"]


class Cancelled(Exception):
    """Der Benutzer hat den Vorgang abgebrochen."""


@dataclass
class OpenSCADInfo:
    found: bool = False
    path: str = ""
    version: str = ""
    year: int = 0
    manifold_flag: str = ""       # z. B. "--backend=manifold"
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "found": self.found,
            "path": self.path,
            "version": self.version,
            "manifold": bool(self.manifold_flag),
            "error": self.error,
        }


@dataclass
class RenderResult:
    ok: bool
    output: str = ""
    log: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    echoes: list[str] = field(default_factory=list)
    seconds: float = 0.0
    returncode: int | None = None

    @property
    def serious_warnings(self) -> list[str]:
        return [w for w in self.warnings if is_serious_warning(w)]

    def problems(self) -> list[str]:
        """Fehler + ernste Warnungen – das, was die KI reparieren soll."""
        return self.errors + self.serious_warnings

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
            "echoes": self.echoes[:50],
            "seconds": round(self.seconds, 2),
            "log": self.log[-20000:],
        }


def is_serious_warning(line: str) -> bool:
    low = line.lower()
    return any(re.search(p, low) for p in SERIOUS_WARNING_PATTERNS)


# ---------------------------------------------------------------------------
# Programm finden
# ---------------------------------------------------------------------------

def _prefer_console(path: str) -> str:
    """Unter Windows liefert nur openscad.com eine Konsolenausgabe."""
    if os.name == "nt" and path.lower().endswith(".exe"):
        com = path[:-4] + ".com"
        if os.path.isfile(com):
            return com
    return path


def find_openscad(custom: str = "") -> str | None:
    if custom:
        custom = os.path.expanduser(custom.strip().strip('"'))
        if os.path.isdir(custom):
            # Ordner angegeben: Programm darin suchen
            for name in ("openscad.com", "openscad.exe", "openscad", "OpenSCAD",
                         "Contents/MacOS/OpenSCAD"):
                candidate = os.path.join(custom, name)
                if os.path.isfile(candidate):
                    return _prefer_console(candidate)
        if os.path.isfile(custom):
            return _prefer_console(custom)
        return None

    env = os.environ.get("OPENSCAD_PATH") or os.environ.get("OPENSCAD")
    if env and os.path.isfile(env):
        return _prefer_console(env)

    for name in LINUX_NAMES:
        found = shutil.which(name)
        if found:
            return _prefer_console(found)

    candidates: list[str] = []
    if os.name == "nt":
        roots = {os.environ.get(v, "") for v in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)")}
        roots |= {r"C:\Program Files", r"C:\Program Files (x86)"}
        local = os.environ.get("LOCALAPPDATA")
        if local:
            roots.add(os.path.join(local, "Programs"))
        for root in sorted(r for r in roots if r):
            candidates += [c.format(pf=root) for c in WINDOWS_CANDIDATES]
    elif sys.platform == "darwin":
        candidates += [os.path.expanduser(c) for c in MAC_CANDIDATES]
    else:
        candidates += [
            "/usr/bin/openscad", "/usr/local/bin/openscad", "/snap/bin/openscad",
            os.path.expanduser("~/Applications/OpenSCAD*.AppImage"),
            os.path.expanduser("~/Downloads/OpenSCAD*.AppImage"),
        ]
    for pattern in candidates:
        for match in sorted(glob.glob(pattern), reverse=True):  # neueste zuerst
            if os.path.isfile(match):
                return _prefer_console(match)
    return None


def _creationflags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


_detect_lock = threading.Lock()
_detect_cache: dict[str, OpenSCADInfo] = {}


def detect(custom: str = "", refresh: bool = False) -> OpenSCADInfo:
    """Findet OpenSCAD und ermittelt Version und Fähigkeiten (gecacht)."""
    with _detect_lock:
        if not refresh and custom in _detect_cache:
            return _detect_cache[custom]
        info = OpenSCADInfo()
        path = find_openscad(custom)
        if not path:
            info.error = ("OpenSCAD wurde nicht gefunden. Bitte in den Einstellungen den Pfad "
                          "zur openscad.exe bzw. OpenSCAD.app angeben.")
            _detect_cache[custom] = info
            return info
        info.path = path
        try:
            proc = subprocess.run([path, "--version"], capture_output=True, text=True,
                                  errors="replace", timeout=60, creationflags=_creationflags())
            text = (proc.stdout + proc.stderr).strip()
            match = re.search(r"version\s+([0-9][0-9.\-a-zA-Z]*)", text)
            info.version = match.group(1) if match else text.splitlines()[-1] if text else "?"
            year = re.match(r"(\d{4})", info.version)
            info.year = int(year.group(1)) if year else 0
            info.found = True
        except (OSError, subprocess.SubprocessError) as exc:
            info.error = f"OpenSCAD konnte nicht gestartet werden: {exc}"
            _detect_cache[custom] = info
            return info
        try:
            proc = subprocess.run([path, "--help"], capture_output=True, text=True,
                                  errors="replace", timeout=60, creationflags=_creationflags())
            help_text = proc.stdout + proc.stderr
            if "--backend" in help_text:
                info.manifold_flag = "--backend=manifold"
            elif "manifold" in help_text.lower():
                info.manifold_flag = "--enable=manifold"
        except (OSError, subprocess.SubprocessError):
            pass
        _detect_cache[custom] = info
        return info


# ---------------------------------------------------------------------------
# Ausführen
# ---------------------------------------------------------------------------

def _needs_virtual_display() -> bool:
    return (sys.platform.startswith("linux") and not os.environ.get("DISPLAY")
            and not os.environ.get("WAYLAND_DISPLAY") and shutil.which("xvfb-run") is not None)


def _run(cmd: list[str], timeout: float, cancel: threading.Event | None,
         cwd: str | None = None) -> tuple[int | None, str]:
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=cwd,
                            creationflags=_creationflags())
    chunks: list[bytes] = []
    reader = threading.Thread(target=lambda: chunks.append(proc.stdout.read()), daemon=True)
    reader.start()
    start = time.monotonic()
    try:
        while proc.poll() is None:
            if cancel is not None and cancel.is_set():
                proc.kill()
                raise Cancelled()
            if time.monotonic() - start > timeout:
                proc.kill()
                reader.join(5)
                proc.stdout.close()
                out = b"".join(chunks).decode("utf-8", errors="replace")
                return None, out + f"\nERROR: Zeitlimit von {int(timeout)} s überschritten – Rendern abgebrochen."
            time.sleep(0.1)
    finally:
        if proc.poll() is None:
            proc.kill()
    reader.join(10)
    proc.stdout.close()
    return proc.returncode, b"".join(chunks).decode("utf-8", errors="replace")


def parse_log(log: str) -> tuple[list[str], list[str], list[str]]:
    errors, warnings, echoes = [], [], []
    for raw in log.splitlines():
        line = raw.strip()
        if not line:
            continue
        upper = line.upper()
        if upper.startswith("ERROR") or "PARSER ERROR" in upper or upper.startswith("CGAL ERROR"):
            errors.append(line)
        elif upper.startswith("WARNING"):
            warnings.append(line)
        elif upper.startswith("ECHO"):
            echoes.append(line)
        elif "top level object is empty" in line.lower():
            errors.append("ERROR: Das Modell ist leer (Current top level object is empty).")
        elif "top level object is not a 3d object" in line.lower():
            errors.append("ERROR: Das Modell ist nur 2D – für STL wird ein 3D-Objekt "
                          "benötigt (z. B. linear_extrude verwenden).")
    return errors, warnings, echoes


def render(scad_file: str | Path, out_file: str | Path, info: OpenSCADInfo, *,
           use_manifold: bool = True, timeout: float = 600, defines: dict[str, str] | None = None,
           cancel: threading.Event | None = None) -> RenderResult:
    """Rendert eine .scad-Datei zu STL (binär) oder 3MF.

    defines: überschreibt Variablen wie auf der Kommandozeile (-D name=wert),
    z. B. {"part": '"leg"'} für den Export eines einzelnen Druckteils.
    """
    scad_file, out_file = Path(scad_file), Path(out_file)
    if not info.found:
        return RenderResult(ok=False, errors=[info.error or "OpenSCAD nicht gefunden."])
    if out_file.exists():
        out_file.unlink()
    out_file.parent.mkdir(parents=True, exist_ok=True)
    cmd = [info.path, "-o", str(out_file)]
    if out_file.suffix.lower() == ".stl":
        cmd += ["--export-format", "binstl"]
    if use_manifold and info.manifold_flag:
        cmd.append(info.manifold_flag)
    for name, value in (defines or {}).items():
        cmd += ["-D", f"{name}={value}"]
    cmd.append(str(scad_file))
    start = time.monotonic()
    code, log = _run(cmd, timeout, cancel, cwd=str(scad_file.parent))
    result = RenderResult(ok=False, output=str(out_file), log=log, returncode=code,
                          seconds=time.monotonic() - start)
    result.errors, result.warnings, result.echoes = parse_log(log)
    if code is None and not result.errors:
        result.errors.append("ERROR: Zeitlimit überschritten.")
    produced = out_file.exists() and out_file.stat().st_size > 84
    if code not in (0, None) and not result.errors:
        result.errors.append(f"ERROR: OpenSCAD wurde mit Code {code} beendet.")
    if code == 0 and not produced and not result.errors:
        result.errors.append("ERROR: OpenSCAD hat keine Datei erzeugt – ist das Modell leer?")
    result.ok = code == 0 and produced and not result.errors
    return result


def render_png(model_file: str | Path, out_png: str | Path, info: OpenSCADInfo, *,
               view: str = "iso", size: tuple[int, int] = (800, 600), timeout: float = 180,
               cancel: threading.Event | None = None) -> RenderResult:
    """Erzeugt ein Vorschaubild. STL-Dateien werden per import() geladen (schnell)."""
    model_file, out_png = Path(model_file), Path(out_png)
    rotations = {"iso": "55,0,25", "front": "90,0,0", "top": "0,0,0", "side": "90,0,90"}
    tmp_dir = None
    scad = model_file
    if model_file.suffix.lower() == ".stl":
        tmp_dir = tempfile.mkdtemp(prefix="scadstudio-")
        scad = Path(tmp_dir) / "preview.scad"
        stl_path = str(model_file.resolve()).replace("\\", "/").replace('"', '\\"')
        scad.write_text(f'color("#d8b56d") import("{stl_path}", convexity = 10);\n', encoding="utf-8")
    cmd = [info.path, "-o", str(out_png), f"--imgsize={size[0]},{size[1]}",
           f"--camera=0,0,0,{rotations.get(view, rotations['iso'])},0", "--viewall", "--autocenter",
           "--colorscheme=Tomorrow Night", "--projection=p", str(scad)]
    if _needs_virtual_display():
        cmd = ["xvfb-run", "-a", "-s", "-screen 0 1280x1024x24"] + cmd
    start = time.monotonic()
    try:
        code, log = _run(cmd, timeout, cancel, cwd=str(scad.parent))
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    result = RenderResult(ok=False, output=str(out_png), log=log, returncode=code,
                          seconds=time.monotonic() - start)
    result.errors, result.warnings, result.echoes = parse_log(log)
    result.ok = code == 0 and out_png.exists() and out_png.stat().st_size > 0
    return result


# ---------------------------------------------------------------------------
# Programme öffnen
# ---------------------------------------------------------------------------

def gui_executable(info: OpenSCADInfo) -> str:
    path = info.path
    if os.name == "nt" and path.lower().endswith(".com"):
        exe = path[:-4] + ".exe"
        if os.path.isfile(exe):
            return exe
    return path


def open_in_openscad(scad_file: str | Path, info: OpenSCADInfo) -> None:
    if not info.found:
        raise RuntimeError(info.error or "OpenSCAD nicht gefunden.")
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen([gui_executable(info), str(scad_file)], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, **kwargs)


def open_folder(path: str | Path) -> None:
    path = str(path)
    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
