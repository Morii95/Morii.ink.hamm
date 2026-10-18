"""Findet KI-Programme (Ollama, Claude Code, Gemini CLI, Codex, Node.js) – auch außerhalb des PATH,
z. B. auf Laufwerk D:, und startet Ollama bei Bedarf automatisch."""
import json
import os
import shutil
import string
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

# Dateinamen je Programm (Windows-Varianten zuerst)
NAMEN = {
    "ollama": ["ollama.exe", "ollama"],
    "claude": ["claude.exe", "claude.cmd", "claude"],
    "gemini": ["gemini.cmd", "gemini.exe", "gemini"],
    "codex": ["codex.cmd", "codex.exe", "codex"],
    "node": ["node.exe", "node"],
    "npm": ["npm.cmd", "npm"],
}

# Desktop-Apps lassen sich nicht fernsteuern – erkennen, um einen klaren Hinweis zu geben
DESKTOP_APPS = {"chatgpt.exe": "ChatGPT-Desktop-App", "gemini.exe": "Gemini-Desktop-App",
                "claude.exe": "Claude-Desktop-App"}
# Ordner, in denen eine gleichnamige Datei die Desktop-App ist und nicht das Kommandozeilen-Programm
DESKTOP_ORDNER = ("anthropicclaude", "windowsapps", "openai.chatgpt")

UEBERSPRINGEN = {
    "windows", "$recycle.bin", "system volume information", "recovery", "perflogs", "msocache",
    ".git", "__pycache__", ".cache", "temp", "tmp", "proc", "sys", "dev", "snap",
    "steamapps", "games", "videos", "music", "pictures", "bilder", "fotos",
    ".vscode", ".gradle", ".m2", ".nuget", ".cargo", ".rustup", ".android", ".docker", ".ollama",
}


def _ausfuehrbar(p: Path) -> bool:
    if not p.is_file():
        return False
    return os.name == "nt" or os.access(p, os.X_OK)


class Programme:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.datei = Path(cfg["daten_pfad"]) / "programme.json"
        self.gefunden: dict[str, str] = {}
        self.apps: dict[str, str] = {}  # gefundene Desktop-Apps (nur zur Info)
        self.lock = threading.Lock()
        self.suche_laeuft = False
        self.letzte_suche = 0.0
        self._ollama_versuch = 0.0
        self.ollama_meldung = ""
        try:
            gespeichert = json.loads(self.datei.read_text(encoding="utf-8"))
            self.gefunden = {k: v for k, v in gespeichert.get("gefunden", {}).items() if Path(v).exists()}
            self.letzte_suche = float(gespeichert.get("zeit", 0))
        except (OSError, ValueError, AttributeError):
            self.gefunden = {}

    # ---------------------------------------------------------- Pfade
    def ordner(self) -> list[str]:
        """Zusätzliche Suchordner: aus config.json + Fundorte der automatischen Suche."""
        eigene = [os.path.expandvars(os.path.expanduser(p)) for p in self.cfg.get("programme", {}).get("pfade", [])]
        fund = [str(Path(p).parent) for p in self.gefunden.values()]
        return list(dict.fromkeys(p for p in eigene + fund if p and os.path.isdir(p)))

    def finde(self, name: str) -> str | None:
        """Wie shutil.which, sucht aber zusätzlich in den eigenen Ordnern. Absolute Pfade werden geprüft."""
        if not name:
            return None
        if os.path.isabs(name):
            return name if _ausfuehrbar(Path(name)) else None
        treffer = shutil.which(name)
        if treffer:
            return treffer
        extra = self.ordner()
        if extra:
            treffer = shutil.which(name, path=os.pathsep.join(extra))
        if not treffer and name in self.gefunden:
            treffer = self.gefunden[name]
        return treffer

    def umgebung(self, env: dict) -> dict:
        """PATH für Kindprozesse ergänzen (z. B. damit gemini.cmd sein node.exe auf D: findet)."""
        extra = self.ordner()
        if extra:
            env = dict(env)
            env["PATH"] = os.pathsep.join(extra + [env.get("PATH", "")])
        return env

    # ---------------------------------------------------------- Suche auf allen Laufwerken
    def _wurzeln(self) -> list[Path]:
        eigener = self.installationsordner()
        if os.name == "nt":
            laufwerke = [Path(f"{b}:\\") for b in string.ascii_uppercase if os.path.exists(f"{b}:\\")]
            laufwerke.sort(key=lambda p: str(p).upper().startswith("C:"))  # D:, E: … zuerst, C: zuletzt
            feste = [Path(os.path.expandvars(p)) for p in (r"%LOCALAPPDATA%\Programs\Ollama", r"%APPDATA%\npm",
                                                         r"%ProgramFiles%\nodejs", r"%LOCALAPPDATA%\Programs")]
            return [p for p in [eigener] + feste if p.is_dir()] + laufwerke
        return [p for p in (Path.home(), Path("/opt"), Path("/usr/local"), Path("/Applications")) if p.is_dir()]

    def suchen(self, zeitlimit: float = 25, tiefe: int = 4, wurzeln: list[Path] | None = None) -> dict[str, str]:
        """Durchsucht Laufwerke (begrenzte Tiefe und Zeit) nach den Programmen und merkt sich die Fundorte."""
        with self.lock:
            if self.suche_laeuft:
                return dict(self.gefunden)
            self.suche_laeuft = True
        try:
            gesucht = {dateiname.lower(): name for name, liste in NAMEN.items() for dateiname in liste}
            neu: dict[str, str] = {}
            ende = time.time() + zeitlimit

            def pruefe(ordner: Path, dateien):
                desktop = any(w in str(ordner).lower() for w in DESKTOP_ORDNER)
                for d in dateien:
                    klein = d.lower()
                    if klein in DESKTOP_APPS and (desktop or klein != "claude.exe"):
                        self.apps.setdefault(DESKTOP_APPS[klein], str(ordner / d))
                        if desktop or klein in ("chatgpt.exe", "gemini.exe"):
                            continue
                    name = gesucht.get(klein)
                    if name and name not in neu and not desktop and _ausfuehrbar(ordner / d):
                        neu[name] = str(ordner / d)

            for wurzel in wurzeln or self._wurzeln():
                basis_tiefe = len(wurzel.parts)
                for ordner, unter, dateien in os.walk(wurzel, onerror=lambda e: None):
                    if time.time() > ende or len(neu) == len(NAMEN):
                        break
                    o = Path(ordner)
                    pruefe(o, dateien)
                    if any(d.lower() == "node_modules" for d in unter):  # lokale npm-Installation
                        bin_ordner = o / "node_modules" / ".bin"
                        try:
                            pruefe(bin_ordner, os.listdir(bin_ordner))
                        except OSError:
                            pass
                    if len(o.parts) - basis_tiefe >= tiefe:
                        unter[:] = []
                    else:
                        unter[:] = [d for d in unter if d.lower() not in UEBERSPRINGEN and d.lower() != "node_modules"]
            with self.lock:
                self.gefunden.update(neu)
                if tiefe >= 4:  # nur die gründliche Suche zählt für "einmal am Tag"
                    self.letzte_suche = time.time()
                try:
                    self.datei.write_text(json.dumps({"gefunden": self.gefunden, "zeit": self.letzte_suche},
                                                     indent=1, ensure_ascii=False), encoding="utf-8")
                except OSError:
                    pass
            return dict(self.gefunden)
        finally:
            self.suche_laeuft = False

    def installationsordner(self) -> Path:
        """Wohin einrichten.bat installiert (Standard: D:\\KI3D unter Windows, sonst ~/KI3D)."""
        wert = self.cfg.get("programme", {}).get("installationsordner") or ("D:\\KI3D" if os.name == "nt" else "~/KI3D")
        return Path(os.path.expanduser(wert))

    def fehlende(self, namen: list[str]) -> list[str]:
        return [n for n in namen if not self.finde(n)]

    # ---------------------------------------------------------- Ollama automatisch starten
    def ollama_erreichbar(self, timeout=2) -> bool:
        try:
            urllib.request.urlopen(self.cfg["ollama"]["url"].rstrip("/") + "/api/tags", timeout=timeout).close()
            return True
        except OSError:
            return False

    def ollama_starten(self, warten: float = 60) -> tuple[bool, str]:
        """Startet `ollama serve`, wenn Ollama lokal laufen soll, aber nicht antwortet."""
        o = self.cfg["ollama"]
        if not o.get("autostart", True):
            return False, "Autostart ist ausgeschaltet (config.json → ollama.autostart)"
        host = (urlsplit(o["url"]).hostname or "").lower()
        if host not in ("localhost", "127.0.0.1", "::1"):
            return False, "Ollama läuft auf einem anderen Rechner – kein Autostart"
        if time.time() - self._ollama_versuch < 60:
            return False, self.ollama_meldung or "Autostart wurde gerade erst versucht"
        self._ollama_versuch = time.time()
        programm = self.finde(o.get("programm") or "ollama")
        if not programm:
            self.ollama_meldung = ("ollama.exe nicht gefunden – in config.json bei ollama.programm den vollen Pfad "
                                   "eintragen, z. B. D:\\Ollama\\ollama.exe")
            return False, self.ollama_meldung
        optionen = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                    "env": self.umgebung(dict(os.environ))}
        if os.name == "nt":
            optionen["creationflags"] = 0x08000000 | 0x00000200  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
        else:
            optionen["start_new_session"] = True
        try:
            subprocess.Popen([programm, "serve"], **optionen)
        except OSError as e:
            self.ollama_meldung = f"Ollama konnte nicht gestartet werden ({programm}): {e}"
            return False, self.ollama_meldung
        ende = time.time() + warten
        while time.time() < ende:
            if self.ollama_erreichbar(timeout=1):
                self.ollama_meldung = f"automatisch gestartet: {programm}"
                return True, self.ollama_meldung
            time.sleep(0.5)
        self.ollama_meldung = f"{programm} gestartet, antwortet aber noch nicht"
        return False, self.ollama_meldung
