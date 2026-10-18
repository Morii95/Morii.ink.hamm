"""Holt den neuesten Stand von GitHub und ersetzt den Code direkt in diesem Ordner.

Arbeitsplatz, Daten (Gespräche, Index) und .env bleiben unangetastet. Eigene Änderungen an
config.json werden in die neue config.json übernommen; die alte liegt danach als config.vorher.json daneben.

Start: update.bat (Windows) · ./update.sh · python -m dirigent.aktualisieren [--quelle URL-oder-zip]
"""
import argparse
import io
import json
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

BASIS = Path(__file__).resolve().parent.parent
STANDARD_QUELLE = "https://github.com/Morii95/Morii.ink.hamm/archive/refs/heads/claude/optimistic-pasteur-skbdtd.zip"
NIE_ANFASSEN = {"arbeitsplatz", "daten", ".env", "config.json", "__pycache__"}
CODE_ORDNER = ("dirigent", "ui", "tests")
AUSGELIEFERT = "config.ausgeliefert.json"  # Kopie der zuletzt ausgelieferten config.json (in daten/)


def _laden(quelle: str) -> bytes:
    if Path(quelle).is_file():
        return Path(quelle).read_bytes()
    print(f"  Lade {quelle} …")
    with urllib.request.urlopen(quelle, timeout=120) as r:
        return r.read()


def _entpacken(daten: bytes, ziel: Path) -> tuple[Path, str]:
    with zipfile.ZipFile(io.BytesIO(daten)) as zf:
        version = zf.comment.decode("utf-8", "replace").strip()[:12]  # GitHub legt den Commit hier ab
        namen = [n for n in zf.namelist() if "/ki-dirigent/" in f"/{n}"]
        if not namen:
            raise SystemExit("  Fehler: Im Download fehlt der Ordner ki-dirigent.")
        zf.extractall(ziel, members=namen)
    erster = namen[0]
    praefix = erster[:("/" + erster).index("/ki-dirigent/")] + "ki-dirigent/"
    return ziel / praefix, version


def _zusammenfuehren(neu, alt, frueher):
    """Neue Vorgabe als Basis; was der Nutzer gegenüber der früheren Vorgabe geändert hat, bleibt erhalten.
    Listen zählen als ein Wert."""
    if isinstance(neu, dict) and isinstance(alt, dict):
        frueher = frueher if isinstance(frueher, dict) else {}
        ergebnis = dict(neu)
        for k, wert in alt.items():
            if k not in neu:
                if k not in frueher:  # vom Nutzer selbst hinzugefügt
                    ergebnis[k] = wert
                continue
            ergebnis[k] = _zusammenfuehren(neu[k], wert, frueher.get(k))
        return ergebnis
    if frueher is not None and alt != frueher:
        return alt  # vom Nutzer geändert
    return neu


def config_uebernehmen(neu_datei: Path, basis: Path = BASIS) -> str:
    ziel = basis / "config.json"
    ausgeliefert = basis / "daten" / AUSGELIEFERT
    neu = json.loads(neu_datei.read_text(encoding="utf-8"))
    if not ziel.exists():
        shutil.copy2(neu_datei, ziel)
        meldung = "config.json neu angelegt"
    else:
        alt = json.loads(ziel.read_text(encoding="utf-8"))
        frueher = json.loads(ausgeliefert.read_text(encoding="utf-8")) if ausgeliefert.exists() else alt
        ergebnis = _zusammenfuehren(neu, alt, frueher)
        if ergebnis == alt:
            meldung = "config.json unverändert"
        else:
            shutil.copy2(ziel, basis / "config.vorher.json")
            ziel.write_text(json.dumps(ergebnis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            meldung = "config.json aktualisiert (deine Änderungen bleiben, alte Fassung: config.vorher.json)"
    ausgeliefert.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(neu_datei, ausgeliefert)
    return meldung


def aktualisieren(quelle: str = STANDARD_QUELLE, basis: Path = BASIS) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        neu, version = _entpacken(_laden(quelle), Path(tmp))
        kopiert, entfernt = 0, 0
        # alte Code-Dateien entfernen, die es in der neuen Fassung nicht mehr gibt
        for ordner in CODE_ORDNER:
            for datei in (basis / ordner).rglob("*") if (basis / ordner).exists() else []:
                if datei.is_file() and "__pycache__" not in datei.parts and not (neu / datei.relative_to(basis)).exists():
                    datei.unlink()
                    entfernt += 1
        for datei in neu.rglob("*"):
            rel = datei.relative_to(neu)
            if datei.is_dir() or rel.parts[0] in NIE_ANFASSEN:
                continue
            ziel = basis / rel
            ziel.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(datei, ziel)
            kopiert += 1
        config = config_uebernehmen(neu / "config.json", basis) if (neu / "config.json").exists() else "–"
    return {"version": version, "kopiert": kopiert, "entfernt": entfernt, "config": config}


def main():
    ap = argparse.ArgumentParser(prog="dirigent.aktualisieren", description=__doc__.splitlines()[0])
    ap.add_argument("--quelle", default=STANDARD_QUELLE, help="Download-URL oder lokale ZIP-Datei")
    args = ap.parse_args()
    print(f"\n  KI-Dirigent aktualisieren in: {BASIS}")
    try:
        e = aktualisieren(args.quelle)
    except OSError as fehler:
        print(f"  Fehler beim Herunterladen: {fehler}")
        sys.exit(1)
    print(f"  Fertig{' – Version ' + e['version'] if e['version'] else ''}: {e['kopiert']} Dateien aktualisiert, "
          f"{e['entfernt']} veraltete entfernt.")
    print(f"  {e['config']}")
    print("  Arbeitsplatz, Daten und .env wurden nicht verändert.")
    print("  Jetzt den Dirigenten (start.bat) neu starten.\n")


if __name__ == "__main__":
    main()
