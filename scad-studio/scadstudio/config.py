"""Einstellungen von SCAD Studio.

Alles liegt im Benutzerordner (Standard: ~/SCAD-Studio), NICHT im
Programmordner – so landen API-Schlüssel nie versehentlich in Git.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

# Anbieter-Standardmodelle. Alle Werte lassen sich in den Einstellungen ändern.
# Gemini: die Aliase zeigen immer auf das aktuelle Modell (2.5er-Modelle laufen
# im Oktober 2026 aus). Die Liste lässt sich in den Einstellungen live abrufen.
GEMINI_MODELS = ["gemini-flash-latest", "gemini-3.8-flash", "gemini-3.5-flash-lite",
                 "gemini-3.1-pro-preview", "gemini-pro-latest"]
# Nano Banana: 3.1-flash-lite-image hat ein kostenloses Kontingent,
# 3.1-flash-image und 3-pro-image liefern bessere Qualität (Abrechnung nötig).
GEMINI_IMAGE_MODELS = ["gemini-3.1-flash-lite-image", "gemini-3.1-flash-image", "gemini-3-pro-image"]
ANTHROPIC_MODELS = ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5", "claude-fable-5-1"]
OPENAI_MODEL_HINTS = ["qwen3-vl:8b", "gemma4", "qwen3-coder:30b", "gpt-oss:20b"]

DEFAULTS: dict[str, Any] = {
    # KI-Anbieter für die Code-Erzeugung: gemini | anthropic | openai | claude_cli
    "provider": "gemini",
    "gemini_api_key": "",
    "gemini_model": GEMINI_MODELS[0],
    "gemini_image_model": GEMINI_IMAGE_MODELS[0],
    "anthropic_api_key": "",
    "anthropic_model": ANTHROPIC_MODELS[0],
    # OpenAI-kompatibel: OpenAI, OpenRouter, LM Studio oder lokal über Ollama
    "openai_base_url": "http://localhost:11434/v1",
    "openai_api_key": "",
    "openai_model": OPENAI_MODEL_HINTS[0],
    # Lokal installiertes Claude Code (nutzt das Claude-Abo statt API-Schlüssel)
    "claude_cli_path": "",        # leer = automatisch suchen
    "claude_cli_model": "",       # leer = Standard, sonst z. B. opus / sonnet
    "claude_cli_effort": "medium",  # Denktiefe: low | medium | high | xhigh | max
    # Drucker (siehe printers.py)
    "printer": "kobra2neo",
    "bed_x": 220,
    "bed_y": 220,
    "bed_z": 250,
    "nozzle": "0.4",
    "layer_height": "0.2",
    "tolerance": "0.2",           # Spaltmaß pro Seite für Steck-/Schraubverbindungen
    "material": "PLA",
    # OpenSCAD
    "openscad_path": "",          # leer = automatisch suchen
    "openscad_gui_path": "",      # optional: andere Version zum Öffnen (leer = wie oben)
    "use_manifold": True,         # schnelles Manifold-Backend nutzen, falls vorhanden
    "render_timeout": 600,        # Sekunden
    "parallel_renders": 0,        # gleichzeitige OpenSCAD-Prozesse (0 = automatisch)
    # KI-Schleifen
    "repair_attempts": 3,         # Fehler automatisch reparieren lassen
    "visual_rounds": 1,           # Bild-Selbstprüfung nach dem Rendern (0 = aus)
    "fit_check": "auto",          # Passungs-/Kollisionsprüfung: auto (mit Manifold) | on | off
    # Speicherort der Projekte (leer = <App-Ordner>/projekte)
    "projects_dir": "",
}

SECRET_KEYS = ("gemini_api_key", "anthropic_api_key", "openai_api_key")
PATH_KEYS = ("openscad_path", "openscad_gui_path", "claude_cli_path", "projects_dir")
NUMBER_KEYS = ("nozzle", "layer_height", "tolerance")

# Umgebungsvariablen als Alternative zu gespeicherten Schlüsseln
ENV_FALLBACKS = {
    "gemini_api_key": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "anthropic_api_key": ("ANTHROPIC_API_KEY",),
    "openai_api_key": ("OPENAI_API_KEY",),
}


def app_home() -> Path:
    env = os.environ.get("SCAD_STUDIO_HOME")
    home = Path(env).expanduser() if env else Path.home() / "SCAD-Studio"
    home.mkdir(parents=True, exist_ok=True)
    return home


class Settings:
    """Thread-sichere Einstellungen mit JSON-Datei im App-Ordner."""

    def __init__(self, path: Path | None = None):
        self.path = path or app_home() / "settings.json"
        self._lock = threading.Lock()
        self._data: dict[str, Any] = dict(DEFAULTS)
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(stored, dict):
            for key, value in stored.items():
                if key in DEFAULTS:
                    self._data[key] = value

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        # Nur geänderte Werte speichern – neue Standardwerte (z. B. Modellnamen)
        # späterer Versionen kommen so automatisch an.
        changed = {k: v for k, v in self._data.items() if v != DEFAULTS.get(k)}
        tmp.write_text(json.dumps(changed, indent=2, ensure_ascii=False), encoding="utf-8")
        if os.name == "posix":
            os.chmod(tmp, 0o600)  # enthält API-Schlüssel
        os.replace(tmp, self.path)

    def get(self, key: str) -> Any:
        with self._lock:
            value = self._data.get(key, DEFAULTS.get(key))
        if key in ENV_FALLBACKS and not value:
            for env in ENV_FALLBACKS[key]:
                if os.environ.get(env):
                    return os.environ[env]
        return value

    def update(self, changes: dict[str, Any]) -> None:
        """Übernimmt Änderungen aus der Oberfläche.

        Für Schlüssel gilt: "" = unverändert lassen, None = löschen.
        """
        with self._lock:
            for key, value in changes.items():
                if key not in DEFAULTS:
                    continue
                if key in SECRET_KEYS:
                    if value is None:
                        self._data[key] = ""
                    elif isinstance(value, str) and value.strip():
                        self._data[key] = value.strip()
                    continue
                default = DEFAULTS[key]
                if isinstance(default, bool):
                    value = bool(value)
                elif isinstance(default, int):
                    try:   # "220,5" / "220.5" → 220
                        value = int(round(float(str(value).replace(",", "."))))
                    except (TypeError, ValueError):
                        continue
                elif isinstance(default, str):
                    value = "" if value is None else str(value).strip()
                    if key in PATH_KEYS:      # „Als Pfad kopieren“ unter Windows setzt Anführungszeichen
                        value = value.strip('"').strip("'").strip()
                    elif key in NUMBER_KEYS:  # deutsches Komma: 0,6 → 0.6
                        value = value.replace(",", ".")
                self._data[key] = value
            self._save()

    def public(self) -> dict[str, Any]:
        """Einstellungen für die Oberfläche – Schlüssel nur maskiert."""
        with self._lock:
            data = dict(self._data)
        for key in SECRET_KEYS:
            stored = data.get(key) or ""
            effective = self.get(key) or ""
            data[key] = ""
            data[key + "_hint"] = mask(effective) if effective else ""
            data[key + "_source"] = "gespeichert" if stored else ("Umgebungsvariable" if effective else "")
        return data

    def projects_dir(self) -> Path:
        custom = self.get("projects_dir")
        path = Path(custom).expanduser() if custom else app_home() / "projekte"
        path.mkdir(parents=True, exist_ok=True)
        return path


def mask(secret: str) -> str:
    secret = secret.strip()
    if len(secret) <= 8:
        return "•" * len(secret)
    return "•" * 6 + secret[-4:]
