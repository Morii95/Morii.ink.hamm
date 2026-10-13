"""Zugriff auf den gesamten PC – Durchsuchen, Scannen, Index, Suche, Bereitstellen.

Alles hier läuft lokal. Cloud-KIs bekommen nie direkten PC-Zugriff, sondern nur das,
was Ollama für die jeweilige Aufgabe herausgesucht und vorbereitet hat.
"""
import fnmatch
import json
import math
import os
import re
import shutil
import string
import threading
import time
from pathlib import Path

from .arbeitsplatz import ist_text, lese_text

STOPWOERTER = {
    "der", "die", "das", "ein", "eine", "einen", "und", "oder", "mit", "von", "für", "fuer", "auf", "aus", "in", "im",
    "zu", "den", "dem", "des", "ist", "sind", "mir", "mein", "meine", "meinen", "meiner", "bitte", "alle", "datei",
    "dateien", "ordner", "the", "and", "for", "with", "pc", "computer", "finde", "such", "suche", "zeig", "zeige",
}


class PC:
    def __init__(self, cfg: dict, speicher):
        self.cfg = cfg
        self.speicher = speicher
        pc = cfg.get("pc", {})
        self.lese_wurzeln = self._wurzeln(pc.get("lesen", ["~"]))
        self.schreib_wurzeln = self._wurzeln(pc.get("schreiben", []))
        self.sperren = [m.lower() for m in pc.get("sperren", [])]
        self.ueberspringen = {m.lower() for m in pc.get("scan_ueberspringen", [])}
        self.eigene = [Path(cfg["daten_pfad"]).resolve()]  # eigene Datenbank nie ausliefern
        self.job = {"laeuft": False}
        self._job_lock = threading.Lock()
        self.embed = None          # wird vom Dirigenten gesetzt: texte -> vektoren | None
        self.zusammenfassen = None  # wird vom Dirigenten gesetzt: text -> kurzbeschreibung | None

    # ---------------------------------------------------------- Rechte
    @staticmethod
    def _wurzeln(liste) -> list[Path]:
        ergebnis = []
        for eintrag in liste:
            if eintrag == "*":
                if os.name == "nt":
                    ergebnis += [Path(f"{b}:\\") for b in string.ascii_uppercase if os.path.exists(f"{b}:\\")]
                else:
                    ergebnis.append(Path("/"))
            else:
                ergebnis.append(Path(os.path.expanduser(eintrag)).resolve())
        return ergebnis

    def gesperrt(self, p: Path) -> bool:
        for teil in p.parts:
            t = teil.lower()
            if any(fnmatch.fnmatch(t, m) for m in self.sperren):
                return True
        return any(p == e or p.is_relative_to(e) for e in self.eigene)

    def _freigegeben(self, p: Path, wurzeln) -> bool:
        return any(p == w or p.is_relative_to(w) for w in wurzeln)

    def pruefe_lesen(self, pfad: str) -> Path:
        p = Path(os.path.expanduser(str(pfad))).resolve()
        if not self._freigegeben(p, self.lese_wurzeln):
            raise PermissionError(f"{p} ist nicht zum Lesen freigegeben (config.json → pc.lesen)")
        if self.gesperrt(p):
            raise PermissionError(f"{p} ist gesperrt (vertrauliche Daten, config.json → pc.sperren)")
        return p

    def pruefe_schreiben(self, pfad: str) -> Path:
        p = Path(os.path.expanduser(str(pfad))).resolve()
        if not self._freigegeben(p, self.schreib_wurzeln):
            raise PermissionError(f"{p} ist nicht zum Schreiben freigegeben (config.json → pc.schreiben)")
        if self.gesperrt(p):
            raise PermissionError(f"{p} ist gesperrt")
        return p

    def schreibe(self, pfad: str, inhalt: str) -> str:
        p = self.pruefe_schreiben(pfad)
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.is_file():
            shutil.copy2(p, p.with_name(p.name + f".{time.strftime('%Y%m%d-%H%M%S')}.bak"))
        p.write_text(inhalt, encoding="utf-8")
        return str(p)

    # ---------------------------------------------------------- Durchsuchen
    def startpunkte(self) -> list[dict]:
        heim = Path.home()
        kandidaten = [("Persönlicher Ordner", heim)]
        for name in ("Desktop", "Schreibtisch", "Documents", "Dokumente", "Downloads", "Pictures", "Bilder",
                     "OneDrive", "Projekte", "Projects"):
            kandidaten.append((name, heim / name))
        for name in (".claude", ".gemini", ".codex", ".ollama"):
            kandidaten.append((f"KI-Tool {name}", heim / name))
        kandidaten += [(str(w), w) for w in self.lese_wurzeln]
        gesehen, liste = set(), []
        for titel, p in kandidaten:
            try:
                p = p.resolve()
                if p in gesehen or not p.is_dir():
                    continue
                self.pruefe_lesen(str(p))
            except (OSError, PermissionError):
                continue
            gesehen.add(p)
            liste.append({"titel": titel, "pfad": str(p)})
        return liste

    def ordner(self, pfad: str, max_eintraege=3000) -> dict:
        p = self.pruefe_lesen(pfad)
        if not p.is_dir():
            raise NotADirectoryError(str(p))
        eintraege = []
        try:
            kinder = list(os.scandir(p))
        except PermissionError as e:
            raise PermissionError(f"Kein Zugriff auf {p}: {e}") from None
        for e in kinder:
            kind = Path(e.path)
            if self.gesperrt(kind):
                continue
            try:
                ist_ordner = e.is_dir()
                st = e.stat()
            except OSError:
                continue
            eintraege.append({"name": e.name, "pfad": str(kind), "typ": "ordner" if ist_ordner else "datei",
                              "groesse": 0 if ist_ordner else st.st_size, "geaendert": st.st_mtime})
        eintraege.sort(key=lambda x: (x["typ"] != "ordner", x["name"].lower()))
        eltern = str(p.parent) if p.parent != p and self._freigegeben(p.parent, self.lese_wurzeln) else ""
        return {"pfad": str(p), "eltern": eltern, "eintraege": eintraege[:max_eintraege],
                "gekuerzt": len(eintraege) > max_eintraege}

    def lese(self, pfad: str, max_zeichen: int | None = None) -> str:
        p = self.pruefe_lesen(pfad)
        if not p.is_file():
            raise FileNotFoundError(str(p))
        if not ist_text(p):
            raise ValueError(f"{p.name} ist keine Textdatei")
        return lese_text(p, max_zeichen)

    def sammle(self, pfad: str, max_dateien=200, max_zeichen_datei=60000) -> list[tuple[str, str]]:
        """Textinhalte einer Datei oder eines ganzen Ordners (rekursiv) einsammeln."""
        p = self.pruefe_lesen(pfad)
        if p.is_file():
            return [(str(p), self.lese(str(p), max_zeichen_datei))]
        quellen = []
        for ordner, unter, dateien in os.walk(p):
            unter[:] = sorted(d for d in unter if d.lower() not in self.ueberspringen
                              and not self.gesperrt(Path(ordner) / d))
            for d in sorted(dateien):
                datei = Path(ordner) / d
                if self.gesperrt(datei) or not ist_text(datei):
                    continue
                try:
                    if datei.stat().st_size > 5_000_000:
                        continue
                    quellen.append((str(datei), lese_text(datei, max_zeichen_datei)))
                except (OSError, ValueError):
                    continue
                if len(quellen) >= max_dateien:
                    return quellen
        return quellen

    def bereitstellen(self, pfad: str, arbeitsplatz) -> str:
        """Kopiert Datei/Ordner vom PC in den Arbeitsplatz (pc-import/…), damit alle KIs damit arbeiten können."""
        quelle = self.pruefe_lesen(pfad)
        grenze = self.cfg.get("pc", {}).get("bereitstellen_max_mb", 200) * 1024 * 1024
        ziel_rel = f"pc-import/{quelle.name or 'laufwerk'}"
        ziel = arbeitsplatz.pfad(ziel_rel)
        if quelle.is_file():
            if quelle.stat().st_size > grenze:
                raise ValueError("Datei ist größer als bereitstellen_max_mb")
            ziel.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(quelle, ziel)
            return ziel_rel
        summe = 0
        for ordner, unter, dateien in os.walk(quelle):
            unter[:] = [d for d in unter if d.lower() not in self.ueberspringen and not self.gesperrt(Path(ordner) / d)]
            for d in dateien:
                q = Path(ordner) / d
                if self.gesperrt(q):
                    continue
                try:
                    summe += q.stat().st_size
                except OSError:
                    continue
                if summe > grenze:
                    raise ValueError(f"Ordner ist größer als {grenze // 1024 // 1024} MB (bereitstellen_max_mb)")
                z = ziel / q.relative_to(quelle)
                z.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(q, z)
        return ziel_rel

    # ---------------------------------------------------------- Hintergrund-Jobs
    def job_status(self) -> dict:
        return {**self.job, "index_eintraege": self.speicher.index_anzahl()}

    def _starte(self, art: str, pfad: str, ziel):
        p = self.pruefe_lesen(pfad)
        with self._job_lock:
            if self.job.get("laeuft"):
                raise RuntimeError(f"Es läuft bereits: {self.job.get('art')} {self.job.get('pfad')}")
            self.job = {"laeuft": True, "art": art, "pfad": str(p), "gezaehlt": 0, "gesamt": 0,
                        "aktuell": "", "start": time.time(), "fehler": None, "abbrechen": False}
        threading.Thread(target=self._lauf, args=(ziel, p), daemon=True).start()
        return self.job

    def _lauf(self, ziel, p):
        try:
            ziel(p)
        except Exception as e:  # noqa: BLE001
            self.job["fehler"] = str(e)
        finally:
            self.job["laeuft"] = False
            self.job["ende"] = time.time()

    def abbrechen(self):
        self.job["abbrechen"] = True

    def scan_starten(self, pfad: str):
        return self._starte("scan", pfad, self._scan)

    def analyse_starten(self, pfad: str):
        return self._starte("analyse", pfad, self._analyse)

    def _scan(self, wurzel: Path):
        grenze = self.cfg.get("pc", {}).get("scan_max_eintraege", 200000)
        stapel = []
        for ordner, unter, dateien in os.walk(wurzel, onerror=lambda e: None):
            if self.job.get("abbrechen"):
                break
            o = Path(ordner)
            unter[:] = [d for d in unter if d.lower() not in self.ueberspringen and not self.gesperrt(o / d)]
            self.job["aktuell"] = ordner
            for d in unter:
                try:
                    st = (o / d).stat()
                except OSError:
                    continue
                stapel.append((str(o / d), d, ordner, "ordner", "", 0, st.st_mtime, ""))
            for d in dateien:
                datei = o / d
                if self.gesperrt(datei):
                    continue
                try:
                    st = datei.stat()
                except OSError:
                    continue
                vorschau = ""
                if st.st_size < 2_000_000 and ist_text(datei):
                    try:
                        vorschau = lese_text(datei, 300)
                    except (OSError, ValueError):
                        pass
                stapel.append((str(datei), d, ordner, "datei", datei.suffix.lower(), st.st_size, st.st_mtime, vorschau))
            self.job["gezaehlt"] = self.job.get("gezaehlt", 0) + len(unter) + len(dateien)
            if len(stapel) >= 500:
                self.speicher.index_schreiben(stapel)
                stapel = []
            if self.job.get("gezaehlt", 0) >= grenze:
                self.job["fehler"] = f"Grenze von {grenze} Einträgen erreicht (pc.scan_max_eintraege)"
                break
        if stapel:
            self.speicher.index_schreiben(stapel)

    def _analyse(self, wurzel: Path):
        """Ollama liest Textdateien eines Ordners und legt Kurzbeschreibung + Suchvektor an."""
        self._scan(wurzel)
        grenze = self.cfg.get("pc", {}).get("analyse_max_dateien", 300)
        basis = str(wurzel)
        dateien = [d for d in self.speicher.index_unter(basis, limit=grenze * 3)
                   if d["pfad"].startswith(basis) and not d["zusammenfassung"]][:grenze]
        self.job.update(gezaehlt=0, gesamt=len(dateien))
        for d in dateien:
            if self.job.get("abbrechen"):
                break
            self.job["aktuell"] = d["pfad"]
            p = Path(d["pfad"])
            beschreibung = ""
            if ist_text(p):
                try:
                    inhalt = lese_text(p, 4000)
                    beschreibung = self.zusammenfassen(p.name, inhalt) if self.zusammenfassen else inhalt[:300]
                except (OSError, ValueError):
                    beschreibung = ""
            vektor = None
            if self.embed:
                try:
                    vektor = (self.embed([f"{p.name}\n{beschreibung}"]) or [None])[0]
                except Exception:  # noqa: BLE001 – ohne Vektor weiter
                    vektor = None
            self.speicher.index_analyse_setzen(d["pfad"], beschreibung or "-", vektor)
            self.job["gezaehlt"] += 1

    # ---------------------------------------------------------- Suche
    @staticmethod
    def suchwoerter(anfrage: str) -> list[str]:
        woerter = re.findall(r"[\wäöüÄÖÜß.\-]{2,}", anfrage.lower())
        return [w for w in dict.fromkeys(woerter) if w not in STOPWOERTER][:8]

    def suche(self, anfrage: str, limit=10, nur_typ: str | None = None) -> list[dict]:
        woerter = self.suchwoerter(anfrage)
        punkte: dict[str, dict] = {}
        for z in self.speicher.index_suche_text(woerter):
            name, pfad = z["name"].lower(), z["pfad"].lower()
            inhalt = (z["vorschau"] + " " + z["zusammenfassung"]).lower()
            wert = 0.0
            for w in woerter:
                wert += 5 if w in name else 2 if w in pfad else 0
                wert += 1 if w in inhalt else 0
            if z["typ"] == "ordner" and any(w == name for w in woerter):
                wert += 4
            punkte[z["pfad"]] = {**z, "punkte": wert}
        if self.embed:
            mit_vektor = self.speicher.index_mit_vektor()
            if mit_vektor:
                try:
                    q = self.embed([anfrage])[0]
                    qn = math.sqrt(sum(x * x for x in q)) or 1
                    for z in mit_vektor:
                        v = json.loads(z.pop("vektor"))
                        cos = sum(a * b for a, b in zip(q, v)) / (qn * (math.sqrt(sum(x * x for x in v)) or 1))
                        if cos > 0.35:
                            eintrag = punkte.setdefault(z["pfad"], {**z, "punkte": 0.0})
                            eintrag["punkte"] += 8 * cos
                except Exception:  # noqa: BLE001 – Textsuche reicht dann
                    pass
        ergebnis = []
        for z in sorted(punkte.values(), key=lambda x: (-x["punkte"], -x["geaendert"])):
            if z["punkte"] <= 0 or (nur_typ and z["typ"] != nur_typ):
                continue
            p = Path(z["pfad"])
            if not p.exists() or self.gesperrt(p):
                continue
            z.pop("vektor", None)
            ergebnis.append(z)
            if len(ergebnis) >= limit:
                break
        return ergebnis
