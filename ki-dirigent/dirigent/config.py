"""Lädt config.json und .env (API-Schlüssel)."""
import json
import os
from pathlib import Path

BASIS = Path(__file__).resolve().parent.parent

SCHLUESSEL = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
}


def lade_env(pfad: Path) -> None:
    """Minimaler .env-Leser: KEY=wert, # Kommentare. Vorhandene Umgebungsvariablen gewinnen."""
    if not pfad.exists():
        return
    for zeile in pfad.read_text(encoding="utf-8").splitlines():
        zeile = zeile.strip()
        if not zeile or zeile.startswith("#") or "=" not in zeile:
            continue
        name, wert = zeile.split("=", 1)
        name, wert = name.strip(), wert.strip().strip('"').strip("'")
        if name and wert and name not in os.environ:
            os.environ[name] = wert


def api_schluessel(anbieter: str) -> str:
    for name in SCHLUESSEL.get(anbieter, ()):
        if os.environ.get(name):
            return os.environ[name]
    return ""


def _pfad(wert: str) -> Path:
    p = Path(os.path.expanduser(wert))
    return p if p.is_absolute() else BASIS / p


def lade_config(pfad: str | None = None) -> dict:
    lade_env(BASIS / ".env")
    datei = Path(pfad or os.environ.get("DIRIGENT_CONFIG") or BASIS / "config.json")
    cfg = json.loads(datei.read_text(encoding="utf-8"))

    if os.environ.get("OLLAMA_URL"):
        cfg["ollama"]["url"] = os.environ["OLLAMA_URL"]
    cfg["arbeitsplatz_pfad"] = _pfad(cfg.get("arbeitsplatz", "arbeitsplatz"))
    cfg["daten_pfad"] = _pfad(cfg.get("daten", "daten"))
    cfg["arbeitsplatz_pfad"].mkdir(parents=True, exist_ok=True)
    cfg["daten_pfad"].mkdir(parents=True, exist_ok=True)

    for s in cfg.get("spezialisten", []):
        s.setdefault("name", s["id"])
        s.setdefault("kategorien", ["alles"])
        s.setdefault("niveau", 2)
        s.setdefault("kosten", 1)
        s.setdefault("max_tokens", 4096)
        s.setdefault("max_kontext_zeichen", 100000)
        s.setdefault("aktiv", True)
        s.setdefault("extra", {})
        s.setdefault("staerken", "")
        s.setdefault("familie", s["anbieter"])  # welcher KI-Ordner (claude/gemini/chatgpt/ollama)
    return cfg
