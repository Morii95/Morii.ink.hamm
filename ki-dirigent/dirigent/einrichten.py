"""Richtet alles auf einem Laufwerk ein (Standard: D:\\KI3D) und fragt vor jedem Schritt.

  1. Gemini CLI und Codex (und Claude Code, falls es fehlt) per npm nach D:\\KI3D\\npm installieren
  2. Ollama-Modelle nach D:\\KI3D\\ollama\\modelle verschieben (falls sie noch auf C: liegen)
  3. Fehlende Ollama-Modelle für den Dirigenten laden
  4. config.json ergänzen, damit der Dirigent alles findet
  5. Neu installierte Programme einmal zum Anmelden starten

Start: einrichten.bat · python -m dirigent.einrichten [--ziel D:\\KI3D] [--ja] [--trocken]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import anbieter
from .config import BASIS, lade_config
from .programme import Programme

PAKETE = {"gemini": "@google/gemini-cli", "codex": "@openai/codex", "claude": "@anthropic-ai/claude-code"}


class Einrichtung:
    def __init__(self, ziel: str | None, ja: bool, trocken: bool):
        self.cfg = lade_config()
        self.prog = Programme(self.cfg)
        self.ziel = Path(os.path.expanduser(ziel)) if ziel else self.prog.installationsordner()
        self.ja, self.trocken = ja, trocken
        self.neue_pfade: list[str] = []
        self.installiert: list[str] = []

    # ---------------------------------------------------------- Helfer
    def frage(self, text: str) -> bool:
        if self.ja or self.trocken:
            print(f"  {text} [j/N] j")
            return True
        try:
            return input(f"  {text} [j/N] ").strip().lower() in ("j", "ja", "y", "yes")
        except EOFError:
            return False

    def laufe(self, befehl: list[str], env: dict | None = None) -> int:
        print("    > " + " ".join(f'"{b}"' if " " in b else b for b in befehl))
        if self.trocken:
            return 0
        try:
            return subprocess.call(befehl, env=env)
        except OSError as e:
            print(f"    Fehler: {e}")
            return 1

    @staticmethod
    def groesse_gb(ordner: Path) -> float:
        summe = 0
        for wurzel, _, dateien in os.walk(ordner):
            for d in dateien:
                try:
                    summe += (Path(wurzel) / d).stat().st_size
                except OSError:
                    pass
        return summe / 1024 ** 3

    # ---------------------------------------------------------- 1. Programme per npm
    def node_und_npm(self) -> tuple[str | None, str | None]:
        node = self.prog.finde("node")
        npm = None
        if node:
            neben = Path(node).parent / ("npm.cmd" if os.name == "nt" else "npm")
            npm = str(neben) if neben.exists() else None
        return node, npm or self.prog.finde("npm")

    def programme_installieren(self):
        print("\n  1) Kommandozeilen-Programme")
        fehlend = [n for n in ("gemini", "codex", "claude") if not self.prog.finde(n)]
        for n in ("gemini", "codex", "claude"):
            if n not in fehlend:
                print(f"     {n:7} vorhanden: {self.prog.finde(n)}")
        if not fehlend:
            return
        node, npm = self.node_und_npm()
        if not node or not npm:
            print(f"     Node.js fehlt. Installiere Node.js (LTS) von https://nodejs.org und wähle als Zielordner\n"
                  f"     {self.ziel / 'nodejs'} – danach einrichten.bat noch einmal starten.")
            return
        try:
            version = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=20).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            version = "?"
        print(f"     Node.js {version}: {node}")
        if version.startswith("v") and version[1:].split(".")[0].isdigit() and int(version[1:].split(".")[0]) < 20:
            print("     Achtung: Gemini CLI und Codex brauchen Node.js 20 oder neuer.")
        npm_ziel = self.ziel / "npm"
        if not self.frage(f"{', '.join(fehlend)} nach {npm_ziel} installieren?"):
            return
        if not self.trocken:
            npm_ziel.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["PATH"] = os.pathsep.join([str(Path(node).parent), env.get("PATH", "")])
        rc = self.laufe([npm, "install", "-g", "--prefix", str(npm_ziel)] + [PAKETE[n] for n in fehlend], env)
        if rc != 0:
            print("     npm meldet einen Fehler (siehe oben).")
            return
        bin_ordner = str(npm_ziel if os.name == "nt" else npm_ziel / "bin")
        self.neue_pfade.append(bin_ordner)
        self.cfg.setdefault("programme", {}).setdefault("pfade", []).append(bin_ordner)  # für finde()
        self.installiert = fehlend
        print(f"     Installiert nach {bin_ordner}")

    # ---------------------------------------------------------- 2./3. Ollama
    def ollama(self):
        print("\n  2) Ollama")
        programm = self.prog.finde(self.cfg["ollama"].get("programm") or "ollama")
        if not programm:
            print(f"     Ollama nicht gefunden. Installieren: https://ollama.com/download – Zielordner z. B.\n"
                  f"     {self.ziel / 'ollama' / 'programm'} (Installer mit /DIR=\"…\" starten).")
            return
        print(f"     Programm: {programm}")
        self.ollama_programm = programm
        aktuell = Path(os.environ.get("OLLAMA_MODELS") or Path.home() / ".ollama" / "models")
        print(f"     Modelle liegen in: {aktuell}")
        auf_ziel = os.name != "nt" or aktuell.drive.upper() == self.ziel.drive.upper()
        if not auf_ziel and aktuell.exists():
            neu = self.ziel / "ollama" / "modelle"
            gb = self.groesse_gb(aktuell)
            if self.frage(f"Ollama-Modelle ({gb:.1f} GB) nach {neu} verschieben?"):
                self.modelle_verschieben(aktuell, neu)
        elif not auf_ziel:
            neu = self.ziel / "ollama" / "modelle"
            if self.frage(f"Neue Ollama-Modelle künftig in {neu} speichern?"):
                self.setze_ollama_models(neu)

        if not self.prog.ollama_erreichbar() and not self.trocken:
            print("     Starte Ollama …")
            print("     " + self.prog.ollama_starten()[1])
        try:
            vorhanden = anbieter.ollama_modelle(self.cfg)
        except Exception:  # noqa: BLE001
            print("     Ollama antwortet nicht – Modelle können jetzt nicht geladen werden.")
            return
        o = self.cfg["ollama"]
        gewuenscht = list(dict.fromkeys([o.get("router_modell"), o.get("helfer_modell"), o.get("embedding_modell")]
                                        + [s["modell"] for s in self.cfg["spezialisten"] if s["anbieter"] == "ollama"]))
        fehlend = [m for m in gewuenscht if m and not anbieter.ollama_hat(m, vorhanden)]
        print(f"     Installiert: {', '.join(vorhanden) or 'keine'}")
        if fehlend and self.frage(f"Fehlende Modelle laden ({', '.join(fehlend)})? Das sind mehrere GB."):
            for m in fehlend:
                self.laufe([programm, "pull", m], self.prog.umgebung(dict(os.environ)))

    def setze_ollama_models(self, neu: Path):
        if not self.trocken:
            neu.mkdir(parents=True, exist_ok=True)
        os.environ["OLLAMA_MODELS"] = str(neu)
        if os.name == "nt":
            self.laufe(["setx", "OLLAMA_MODELS", str(neu)])
        else:
            print(f"     Bitte in deiner Shell-Konfiguration setzen: export OLLAMA_MODELS=\"{neu}\"")

    def modelle_verschieben(self, alt: Path, neu: Path):
        print("     Beende Ollama für das Verschieben …")
        if os.name == "nt":
            for name in ("ollama app.exe", "ollama.exe"):
                self.laufe(["taskkill", "/F", "/IM", name])
        if not self.trocken:
            neu.parent.mkdir(parents=True, exist_ok=True)
            print(f"     Kopiere nach {neu} … (das kann dauern)")
            shutil.copytree(alt, neu, dirs_exist_ok=True)  # erst kopieren …
            shutil.rmtree(alt)                             # … dann das Original löschen
        self.setze_ollama_models(neu)
        print("     Verschoben. Ollama verwendet ab jetzt den neuen Ordner.")

    # ---------------------------------------------------------- 4. config.json
    def config_ergaenzen(self):
        print("\n  3) config.json")
        datei = BASIS / "config.json"
        cfg = json.loads(datei.read_text(encoding="utf-8"))
        prog = cfg.setdefault("programme", {})
        pfade = prog.setdefault("pfade", [])
        for p in self.neue_pfade:
            if p not in pfade:
                pfade.append(p)
        prog["installationsordner"] = str(self.ziel)
        if getattr(self, "ollama_programm", None) and not cfg["ollama"].get("programm"):
            cfg["ollama"]["programm"] = self.ollama_programm
        print(f"     programme.pfade = {pfade}")
        if not self.trocken:
            datei.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # ---------------------------------------------------------- 5. Anmelden
    def anmelden(self):
        if not self.installiert:
            return
        print("\n  4) Anmelden")
        for name in self.installiert:
            pfad = self.prog.finde(name)
            if pfad and not self.ja and self.frage(f"{name} jetzt einmal starten und anmelden? (danach mit /quit bzw. Strg+C beenden)"):
                self.laufe([pfad], self.prog.umgebung(dict(os.environ)))

    def los(self):
        print(f"\n  KI-Dirigent einrichten – Ziel: {self.ziel}{'  (Probelauf, es wird nichts geändert)' if self.trocken else ''}")
        self.programme_installieren()
        self.ollama()
        self.config_ergaenzen()
        self.anmelden()
        print("\n  Fertig. Jetzt start.bat (neu) starten.\n")


def main():
    ap = argparse.ArgumentParser(prog="dirigent.einrichten", description="Alles auf einem Laufwerk einrichten")
    ap.add_argument("--ziel", help="Installationsordner, Standard D:\\KI3D (Windows) bzw. ~/KI3D")
    ap.add_argument("--ja", action="store_true", help="alle Fragen mit Ja beantworten")
    ap.add_argument("--trocken", action="store_true", help="nur anzeigen, was passieren würde")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass
    Einrichtung(args.ziel, args.ja, args.trocken).los()


if __name__ == "__main__":
    main()
