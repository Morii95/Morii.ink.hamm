"""Gemeinsame Arbeitsfläche auf dem PC + eigene Ordner pro KI (wie ~/.claude)."""
import os
import re
import shutil
import time
from pathlib import Path

TEXT_ENDUNGEN = {
    ".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml", ".toml", ".ini",
    ".cfg", ".conf", ".xml", ".html", ".htm", ".css", ".scss", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
    ".py", ".ipynb", ".java", ".kt", ".c", ".h", ".cpp", ".hpp", ".cs", ".go", ".rs", ".rb", ".php", ".swift",
    ".sql", ".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd", ".lua", ".r", ".m", ".vue", ".svelte", ".tex",
    ".log", ".env.example", ".gitignore", ".dockerfile", ".srt", ".vtt",
}

ANWEISUNGEN = "ANWEISUNGEN.md"
GEDAECHTNIS = "GEDAECHTNIS.md"

# ```datei:pfad/name.ext   …   ```
DATEI_BLOCK = re.compile(r"^```datei:([^\n`]+)\n(.*?)\n```[ \t]*$", re.S | re.M)
MERKEN_BLOCK = re.compile(r"^```merken\n(.*?)\n```[ \t]*$", re.S | re.M)


def ist_text(pfad: Path, kopf: bytes | None = None) -> bool:
    if pfad.suffix.lower() in TEXT_ENDUNGEN or pfad.name in {"Dockerfile", "Makefile", "README", "LICENSE"}:
        return True
    if kopf is None:
        try:
            with open(pfad, "rb") as f:
                kopf = f.read(4096)
        except OSError:
            return False
    if not kopf or b"\x00" in kopf:
        return not kopf
    try:
        kopf.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def lese_text(pfad: Path, max_zeichen: int | None = None) -> str:
    with open(pfad, "rb") as f:
        roh = f.read((max_zeichen * 4 + 16) if max_zeichen else -1)
    if b"\x00" in roh[:8192]:
        raise ValueError(f"{pfad.name} ist keine Textdatei")
    text = roh.decode("utf-8", errors="replace")
    if max_zeichen and len(text) > max_zeichen:
        text = text[:max_zeichen] + f"\n…[gekürzt, Datei hat mehr als {max_zeichen} Zeichen]"
    return text


class Arbeitsplatz:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.wurzel = Path(cfg["arbeitsplatz_pfad"]).resolve()
        self.wurzel.mkdir(parents=True, exist_ok=True)
        self.versionen = self.wurzel / ".versionen"
        self._ki_ordner_anlegen()

    # ---------------------------------------------------------- Pfade
    def pfad(self, rel: str) -> Path:
        rel = (rel or "").strip().replace("\\", "/").lstrip("/")
        if not rel:
            raise ValueError("leerer Pfad")
        ziel = (self.wurzel / rel).resolve()
        if ziel != self.wurzel and not ziel.is_relative_to(self.wurzel):
            raise ValueError("Pfad liegt außerhalb des Arbeitsplatzes")
        return ziel

    def relativ(self, p: Path) -> str:
        return p.resolve().relative_to(self.wurzel).as_posix()

    # ---------------------------------------------------------- KI-Ordner
    def ki_ordner_name(self, anbieter: str) -> str:
        eintrag = self.cfg.get("ki_ordner", {}).get(anbieter) or {}
        return eintrag.get("ordner", anbieter) if isinstance(eintrag, dict) else anbieter

    def _ki_ordner_anlegen(self):
        vorlagen = {
            "gemeinsam": "# Anweisungen für ALLE KIs\n\n<!-- Kurz halten: wird jeder KI bei jeder Aufgabe mitgegeben. -->\n",
        }
        namen = ["gemeinsam"] + [self.ki_ordner_name(a) for a, v in self.cfg.get("ki_ordner", {}).items()
                                 if isinstance(v, dict)]
        for name in namen:
            ordner = self.wurzel / name
            ordner.mkdir(parents=True, exist_ok=True)
            anw = ordner / ANWEISUNGEN
            if not anw.exists():
                anw.write_text(vorlagen.get(name, f"# Anweisungen für {name}\n\n"
                                                  "<!-- Kurz halten: wird bei jeder Aufgabe für diese KI mitgeschickt. -->\n"),
                               encoding="utf-8")
            (ordner / GEDAECHTNIS).touch(exist_ok=True)

    def ki_ordner(self) -> list[dict]:
        """Übersicht für die Oberfläche: Plattform-Ordner + erkannte Tool-Ordner (~/.claude …)."""
        liste = []
        for anbieter, v in self.cfg.get("ki_ordner", {}).items():
            if not isinstance(v, dict):
                continue
            tool = v.get("tool_ordner")
            tool_pfad = Path(os.path.expanduser(tool)) if tool else None
            liste.append({
                "anbieter": anbieter,
                "ordner": self.ki_ordner_name(anbieter),
                "tool_ordner": str(tool_pfad) if tool_pfad else "",
                "tool_ordner_da": bool(tool_pfad and tool_pfad.is_dir()),
            })
        return liste

    def _kurz(self, text: str) -> str:
        text = re.sub(r"<!--.*?-->", "", text, flags=re.S).strip()
        # nur Überschrift ohne Inhalt → nichts senden (spart Tokens)
        if all(z.startswith("#") or not z.strip() for z in text.splitlines()):
            return ""
        grenze = self.cfg.get("ki_ordner", {}).get("max_zeichen", 2000)
        return text if len(text) <= grenze else text[-grenze:]

    def anweisungen_fuer(self, anbieter: str, ist_cli: bool = False) -> str:
        """ANWEISUNGEN + GEDAECHTNIS aus gemeinsam/ und dem KI-Ordner (optional Tool-Ordner).

        `anbieter` ist die KI-Familie (anthropic/openai/gemini/ollama)."""
        teile = []
        for name in ("gemeinsam", self.ki_ordner_name(anbieter)):
            for datei, titel in ((ANWEISUNGEN, "Anweisungen"), (GEDAECHTNIS, "Gedächtnis")):
                p = self.wurzel / name / datei
                if p.exists():
                    inhalt = self._kurz(p.read_text(encoding="utf-8", errors="replace"))
                    if inhalt:
                        teile.append(f"[{titel} – {name}]\n{inhalt}")
        ko = self.cfg.get("ki_ordner", {})
        eintrag = ko.get(anbieter) or {}
        # CLI-Programme lesen ihre Tool-Ordner selbst – nur für API-Aufrufe ergänzen.
        if ko.get("tool_anweisungen_nutzen") and not ist_cli and eintrag.get("tool_anweisungen"):
            p = Path(os.path.expanduser(eintrag.get("tool_ordner", ""))) / eintrag["tool_anweisungen"]
            if p.is_file():
                inhalt = self._kurz(p.read_text(encoding="utf-8", errors="replace"))
                if inhalt:
                    teile.append(f"[Anweisungen aus {p}]\n{inhalt}")
        return "\n\n".join(teile)

    def merken(self, anbieter: str, text: str):
        p = self.wurzel / self.ki_ordner_name(anbieter) / GEDAECHTNIS
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"\n- {time.strftime('%Y-%m-%d')}: " + text.strip().replace("\n", "\n  ") + "\n")

    # ---------------------------------------------------------- Dateien
    def liste(self, max_eintraege=3000) -> list[dict]:
        ergebnis = []
        for ordner, unterordner, dateien in os.walk(self.wurzel):
            unterordner[:] = sorted(d for d in unterordner if not d.startswith("."))
            for d in sorted(dateien):
                if d.startswith("."):
                    continue
                p = Path(ordner) / d
                try:
                    st = p.stat()
                except OSError:
                    continue
                ergebnis.append({"pfad": self.relativ(p), "groesse": st.st_size, "geaendert": st.st_mtime})
                if len(ergebnis) >= max_eintraege:
                    return ergebnis
        return ergebnis

    def lese(self, rel: str, max_zeichen: int | None = None) -> str:
        p = self.pfad(rel)
        if not p.is_file():
            raise FileNotFoundError(rel)
        return lese_text(p, max_zeichen)

    def _sichere_version(self, p: Path):
        if p.is_file():
            ziel = self.versionen / time.strftime("%Y%m%d-%H%M%S") / self.relativ(p)
            ziel.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, ziel)

    def schreibe(self, rel: str, inhalt: str | bytes) -> str:
        p = self.pfad(rel)
        if p.is_relative_to(self.versionen):
            raise ValueError("In .versionen wird nicht geschrieben")
        p.parent.mkdir(parents=True, exist_ok=True)
        self._sichere_version(p)
        if isinstance(inhalt, bytes):
            p.write_bytes(inhalt)
        else:
            p.write_text(inhalt, encoding="utf-8")
        return self.relativ(p)

    def loesche(self, rel: str):
        p = self.pfad(rel)
        if p == self.wurzel:
            raise ValueError("Arbeitsplatz selbst kann nicht gelöscht werden")
        self._sichere_version(p)  # gelöschte Dateien bleiben in .versionen erhalten
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()

    # ---------------------------------------------------------- Antworten auswerten
    def uebernehme_bloecke(self, text: str, anbieter: str, pc=None) -> tuple[list[str], list[str]]:
        """Speichert ```datei:…``` Blöcke und ```merken``` Notizen. Gibt (gespeichert, fehler) zurück."""
        gespeichert, fehler = [], []
        for m in DATEI_BLOCK.finditer(text):
            ziel, inhalt = m.group(1).strip(), m.group(2)
            try:
                if os.path.isabs(ziel) or re.match(r"^[A-Za-z]:[\\/]", ziel):
                    if not pc:
                        raise ValueError("absolute Pfade sind nicht erlaubt")
                    gespeichert.append(pc.schreibe(ziel, inhalt))
                else:
                    gespeichert.append(self.schreibe(ziel, inhalt))
            except (ValueError, PermissionError, OSError) as e:
                fehler.append(f"{ziel}: {e}")
        for m in MERKEN_BLOCK.finditer(text):
            self.merken(anbieter, m.group(1))
            gespeichert.append(f"{self.ki_ordner_name(anbieter)}/{GEDAECHTNIS}")
        return gespeichert, fehler
