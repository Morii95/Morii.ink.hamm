"""Der Dirigent: Ollama plant, sucht, bereitet vor – Spezialisten arbeiten – Ollama führt zusammen."""
import hashlib
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import anbieter, router
from .arbeitsplatz import Arbeitsplatz
from .pc import PC
from .programme import Programme
from .speicher import Speicher


class Abgebrochen(Exception):
    pass


def _hash(*teile) -> str:
    return hashlib.sha256(json.dumps(teile, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def formatiere_quellen(quellen: list[tuple[str, str]], max_zeichen: int) -> str:
    """Anhänge als <datei>-Blöcke; begrenzt auf das Kontextfenster des Ziel-Spezialisten."""
    teile, rest = [], max_zeichen
    for name, text in quellen:
        if rest <= 200:
            teile.append(f'<datei name="{name}">[weggelassen – Kontext voll]</datei>')
            continue
        if len(text) > rest:
            text = text[:rest] + "\n…[gekürzt]"
        rest -= len(text)
        teile.append(f'<datei name="{name}">\n{text}\n</datei>')
    return "\n\n".join(teile)


EXTRAKT_SYSTEM = ("Du bereitest Daten für eine andere KI vor. Gib nur die Stellen des Textes wieder, die für die "
                  "Aufgabe relevant sind – Code, Zahlen, Namen und Daten wörtlich. Lass Irrelevantes weg, keine "
                  "Kommentare. Ist nichts relevant, antworte genau: NICHTS")
VERDICHTEN_SYSTEM = ("Du verdichtest Gesprächsverläufe für eine andere KI. Schreibe höchstens 12 knappe Stichpunkte: "
                     "Fakten, Entscheidungen, Namen, Dateien, Zahlen, offene Punkte. Keine Einleitung.")
ZUSAMMENFUEHREN_SYSTEM = ("Führe die Teilergebnisse zu einer einheitlichen Antwort auf die ursprüngliche Anfrage "
                          "zusammen. Inhalte, Code und Zahlen unverändert übernehmen; nur ordnen, Doppeltes entfernen "
                          "und Übergänge glätten. Keine Einleitung.")
BESCHREIBEN_SYSTEM = ("Beschreibe in einem Satz (höchstens 25 Wörter), was diese Datei enthält. Nenne wichtige "
                      "Begriffe, Namen und Jahreszahlen. Keine Einleitung.")


class Dirigent:
    def __init__(self, cfg: dict, chat_fn=None, modelle_fn=None):
        self.cfg = cfg
        self.speicher = Speicher(Path(cfg["daten_pfad"]) / "dirigent.db")
        self.arbeitsplatz = Arbeitsplatz(cfg)
        self.pc = PC(cfg, self.speicher)
        self.pc.embed = self._embed
        self.pc.zusammenfassen = self._datei_beschreiben
        self.programme = Programme(cfg)
        cfg["_programme"] = self.programme
        self._chat = chat_fn or anbieter.chat
        self._modelle = modelle_fn or anbieter.ollama_modelle
        self._autostart = modelle_fn is None  # in Tests kein echtes Ollama starten
        self._ollama = (0.0, False, [], "")
        self._lock = threading.Lock()

    # ================================================================ Verfügbarkeit
    def ollama_status(self, neu=False):
        zeit, ok, modelle, fehler = self._ollama
        if neu or time.time() - zeit > 20:
            try:
                modelle, ok, fehler = self._modelle(self.cfg), True, ""
            except Exception as e:  # noqa: BLE001
                modelle, ok, fehler = [], False, str(e)
                if self._autostart:  # Ollama im Hintergrund starten, nächste Prüfung sieht es dann
                    threading.Thread(target=self.programme.ollama_starten, daemon=True).start()
            self._ollama = (time.time(), ok, modelle, fehler)
        return ok, modelle, fehler

    def spezialist_status(self, s: dict) -> tuple[bool, str]:
        if s.get("aktiv") is False:
            return False, "deaktiviert (config.json)"
        a = s["anbieter"]
        if a == "ollama":
            ok, modelle, _ = self.ollama_status()
            if not ok:
                return False, "Ollama nicht erreichbar"
            if not anbieter.ollama_hat(s["modell"], modelle):
                return False, f"Modell fehlt: ollama pull {s['modell']}"
            return True, "bereit (lokal)"
        if a == "cli":
            pfad = anbieter.cli_pfad(s.get("befehl") or [], self.cfg)
            return (True, f"Programm gefunden: {pfad}") if pfad else (False, f"Programm '{(s.get('befehl') or ['?'])[0]}' nicht gefunden")
        if a in ("anthropic", "openai", "gemini"):
            from .config import api_schluessel
            return (True, "API-Schlüssel vorhanden") if api_schluessel(a) else (False, "kein API-Schlüssel (.env)")
        return False, f"unbekannter Anbieter {a}"

    def benoetigte_programme(self) -> list[str]:
        namen = ["ollama"] if self.cfg["ollama"].get("autostart", True) else []
        namen += [s["befehl"][0] for s in self.cfg["spezialisten"]
                  if s["anbieter"] == "cli" and s.get("aktiv") is not False and s.get("befehl")]
        return list(dict.fromkeys(namen))

    def programme_suchen(self, zeitlimit: float = 25) -> dict:
        self.programme.suchen(zeitlimit=zeitlimit)
        if not self.ollama_status(neu=True)[0] and self._autostart:
            self.programme.ollama_starten()
        self.ollama_status(neu=True)
        return {"gefunden": self.programme.gefunden, "fehlen": self.programme.fehlende(self.benoetigte_programme())}

    def verfuegbar(self) -> list[dict]:
        return [s for s in self.cfg["spezialisten"] if self.spezialist_status(s)[0]]

    def _lokales_modell(self, wunsch: str) -> str | None:
        ok, modelle, _ = self.ollama_status()
        if not ok or not modelle:
            return None
        if anbieter.ollama_hat(wunsch, modelle):
            return wunsch
        chat_modelle = [m for m in modelle if "embed" not in m.lower()]
        return chat_modelle[0] if chat_modelle else None

    def router_spez(self):
        m = self._lokales_modell(self.cfg["ollama"].get("router_modell", ""))
        return {"id": "router", "name": "Ollama Router", "anbieter": "ollama", "familie": "ollama", "modell": m,
                "max_tokens": 600, "extra": {}} if m else None

    def helfer_spez(self, max_tokens=1500):
        m = self._lokales_modell(self.cfg["ollama"].get("helfer_modell", ""))
        return {"id": "helfer", "name": "Ollama Helfer", "anbieter": "ollama", "familie": "ollama", "modell": m,
                "max_tokens": max_tokens, "extra": {}} if m else None

    def embedding_modell(self) -> str | None:
        wunsch = self.cfg["ollama"].get("embedding_modell", "")
        ok, modelle, _ = self.ollama_status()
        return wunsch if ok and wunsch and anbieter.ollama_hat(wunsch, modelle) else None

    def status(self) -> dict:
        ok, modelle, fehler = self.ollama_status(neu=True)
        spez = []
        for s in self.cfg["spezialisten"]:
            bereit, grund = self.spezialist_status(s)
            spez.append({k: s.get(k) for k in ("id", "name", "anbieter", "familie", "modell", "kosten", "niveau",
                                                "kategorien", "staerken")} | {"verfuegbar": bereit, "grund": grund})
        r, h = self.router_spez(), self.helfer_spez()
        sparen = self.cfg["sparen"]
        return {
            "programme": {"gefunden": self.programme.gefunden, "ordner": self.programme.ordner(),
                          "suche_laeuft": self.programme.suche_laeuft},
            "ollama": {"erreichbar": ok, "url": self.cfg["ollama"]["url"], "modelle": modelle, "fehler": fehler,
                       "autostart": self.programme.ollama_meldung,
                       "router": r["modell"] if r else None, "helfer": h["modell"] if h else None,
                       "embedding": self.embedding_modell()},
            "spezialisten": spez,
            "strategie": sparen.get("strategie", "sparsam"),
            "zerlegen": sparen.get("zerlegen", True),
            "arbeitsplatz": str(self.arbeitsplatz.wurzel),
            "ki_ordner": self.arbeitsplatz.ki_ordner(),
            "pc": {"lesen": [str(p) for p in self.pc.lese_wurzeln], "schreiben": [str(p) for p in self.pc.schreib_wurzeln],
                   "index": self.speicher.index_anzahl(), "job": {k: v for k, v in self.pc.job.items() if k != "abbrechen"}},
        }

    # ================================================================ Aufrufe mit Zählung & Cache
    def rufe(self, spez, system, nachrichten, zweck, on_delta=None, json_schema=None, cache=True,
             zaehler=None, abbruch=None) -> anbieter.Antwort:
        darf_cache = cache and self.cfg["sparen"].get("cache", True) and spez["anbieter"] != "cli" and not json_schema
        schluessel = _hash(spez["anbieter"], spez.get("modell"), spez.get("befehl"), system, nachrichten,
                           spez.get("max_tokens")) if darf_cache else None
        if schluessel:
            treffer = self.speicher.cache_lesen("antwort:" + schluessel)
            if treffer is not None:
                if on_delta:
                    on_delta(treffer)
                self.speicher.verbrauch_hinzu(spez["anbieter"], spez.get("modell") or "", spez["id"], zweck, 0, 0, True)
                self._zaehle(zaehler, spez, 0, 0, cache=True)
                return anbieter.Antwort(treffer, 0, 0, spez["anbieter"], spez.get("modell") or "", roh={"cache": True})
        antwort = self._chat(self.cfg, spez, system, nachrichten, on_delta=on_delta, json_schema=json_schema,
                             arbeitsordner=self.arbeitsplatz.wurzel, abbruch=abbruch)
        self.speicher.verbrauch_hinzu(spez["anbieter"], spez.get("modell") or antwort.modell, spez["id"], zweck,
                                      antwort.eingabe_tokens, antwort.ausgabe_tokens, False, antwort.geschaetzt)
        self._zaehle(zaehler, spez, antwort.eingabe_tokens, antwort.ausgabe_tokens)
        if schluessel and antwort.text.strip():
            self.speicher.cache_schreiben("antwort:" + schluessel, antwort.text)
        return antwort

    def _zaehle(self, zaehler, spez, ein, aus, cache=False):
        if zaehler is None:
            return
        with self._lock:
            art = "lokal" if spez["anbieter"] == "ollama" else "extern"
            zaehler[art] = zaehler.get(art, 0) + (ein or 0) + (aus or 0)
            if cache:
                zaehler["cache_treffer"] = zaehler.get("cache_treffer", 0) + 1

    # ================================================================ Lokale Helfer (Ollama)
    def _embed(self, texte):
        modell = self.embedding_modell()
        return anbieter.ollama_embed(self.cfg, modell, texte) if modell else None

    def _datei_beschreiben(self, name: str, inhalt: str) -> str:
        helfer = self.helfer_spez(max_tokens=80)
        if not helfer:
            return inhalt[:300]
        a = self.rufe(helfer, BESCHREIBEN_SYSTEM, [{"role": "user", "content": f"Datei: {name}\n\n{inhalt}"}],
                      "pc-analyse")
        return a.text.strip()[:400]

    def _verdichte(self, bisher: str, block: list[dict], emit, zaehler) -> str:
        schluessel = "verdichtet:" + _hash(bisher, block)
        treffer = self.speicher.cache_lesen(schluessel)
        if treffer is not None:
            return treffer
        text = "\n\n".join(f"{'Nutzer' if n['role'] == 'user' else 'KI'}: {n['content'][:3000]}" for n in block)
        neu = None
        helfer = self.helfer_spez(max_tokens=600)
        if helfer:
            emit("status", {"text": "Ollama verdichtet den Gesprächsverlauf …"})
            try:
                neu = self.rufe(helfer, VERDICHTEN_SYSTEM,
                                [{"role": "user", "content": f"Bisherige Zusammenfassung:\n{bisher or '-'}\n\nNeuer Verlauf:\n{text}"}],
                                "verdichten", cache=False, zaehler=zaehler).text.strip()
            except Exception:  # noqa: BLE001
                neu = None
        if not neu:
            neu = (bisher + "\n" + "\n".join(f"- {n['role']}: {n['content'][:200]}" for n in block)).strip()[-3000:]
        self.speicher.cache_schreiben(schluessel, neu)
        return neu

    def _verlauf_kontext(self, verlauf: list[dict], emit, zaehler) -> tuple[str, list[dict]]:
        """Ältere Nachrichten werden lokal verdichtet, die letzten bleiben wörtlich (gekürzt) erhalten."""
        s = self.cfg["sparen"]
        letzte, block, maxn = s.get("verlauf_letzte", 4), 6, s.get("max_nachricht_zeichen", 6000)

        def kurz(n):
            c = n["content"]
            return {"role": n["role"], "content": c if len(c) <= maxn else c[:maxn // 2] + "\n…[gekürzt]…\n" + c[-maxn // 2:]}

        if sum(len(n["content"]) for n in verlauf) <= s.get("verdichten_ab_zeichen", 6000):
            return "", [kurz(n) for n in verlauf]
        alt = verlauf[:max(0, len(verlauf) - letzte)]
        bloecke = len(alt) // block
        zusammenfassung = ""
        for i in range(bloecke):
            zusammenfassung = self._verdichte(zusammenfassung, alt[i * block:(i + 1) * block], emit, zaehler)
        return zusammenfassung, [kurz(n) for n in verlauf[bloecke * block:]]

    def _vorbereiten(self, aufgabe: str, quellen, spez, emit, zaehler, abbruch) -> tuple[str, dict]:
        """Ollama extrahiert aus großen Anhängen nur das, was die Aufgabe braucht."""
        if not quellen:
            return "", {}
        s = self.cfg["sparen"]
        gesamt = sum(len(t) for _, t in quellen)
        helfer = self.helfer_spez(max_tokens=1500)
        if (spez["anbieter"] == "ollama" or not helfer or gesamt <= s.get("vorbereiten_ab_zeichen", 12000)
                or gesamt > s.get("vorbereiten_bis_zeichen", 250000)):
            return formatiere_quellen(quellen, spez["max_kontext_zeichen"]), {}
        neue = []
        stueck_groesse = 6000
        for name, text in quellen:
            if len(text) <= 2000:
                neue.append((name, text))
                continue
            auszuege = []
            stuecke = [text[i:i + stueck_groesse] for i in range(0, len(text), stueck_groesse)]
            for nr, stueck in enumerate(stuecke, 1):
                if abbruch and abbruch.is_set():
                    raise Abgebrochen()
                emit("status", {"text": f"Ollama bereitet {Path(name).name} vor ({nr}/{len(stuecke)}) …"})
                schluessel = "auszug:" + _hash(aufgabe, stueck)
                auszug = self.speicher.cache_lesen(schluessel)
                if auszug is None:
                    try:
                        auszug = self.rufe(helfer, EXTRAKT_SYSTEM,
                                           [{"role": "user", "content": f"Aufgabe: {aufgabe[:1500]}\n\nText:\n{stueck}"}],
                                           "vorbereiten", cache=False, zaehler=zaehler).text.strip()
                    except Exception:  # noqa: BLE001 – lieber Original schicken als nichts
                        auszug = stueck
                    self.speicher.cache_schreiben(schluessel, auszug)
                if auszug and auszug.upper().strip(" .") != "NICHTS":
                    auszuege.append(auszug)
            if auszuege:
                neue.append((f"{name} (Auszug, lokal vorbereitet)", "\n…\n".join(auszuege)))
        ergebnis = formatiere_quellen(neue, spez["max_kontext_zeichen"])
        info = {"vorher_zeichen": gesamt, "nachher_zeichen": len(ergebnis)}
        emit("vorbereitung", info)
        return ergebnis, info

    # ================================================================ Anhänge & PC-Suche
    def _anhaenge_laden(self, arbeitsplatz_dateien, pc_pfade, emit) -> list[tuple[str, str]]:
        quellen, max_z = [], self.cfg["sparen"].get("max_datei_zeichen", 60000)
        for rel in arbeitsplatz_dateien or []:
            try:
                p = self.arbeitsplatz.pfad(rel)
                if p.is_dir():
                    for unter in sorted(p.rglob("*")):
                        if unter.is_file() and not any(t.startswith(".") for t in unter.relative_to(p).parts):
                            try:
                                quellen.append((self.arbeitsplatz.relativ(unter), self.arbeitsplatz.lese(self.arbeitsplatz.relativ(unter), max_z)))
                            except (ValueError, OSError):
                                continue
                else:
                    quellen.append((rel, self.arbeitsplatz.lese(rel, max_z)))
            except (ValueError, OSError) as e:
                emit("hinweis", {"text": f"Anhang {rel}: {e}"})
        for pfad in pc_pfade or []:
            try:
                quellen += self.pc.sammle(pfad, max_zeichen_datei=max_z)
            except (ValueError, OSError, PermissionError) as e:
                emit("hinweis", {"text": f"PC {pfad}: {e}"})
        return quellen

    def _pc_suchen(self, suchbegriffe: str, emit) -> list[tuple[str, str]]:
        emit("status", {"text": f"Ollama sucht auf dem PC: {suchbegriffe} …"})
        treffer = self.pc.suche(suchbegriffe, limit=8)
        emit("pc_suche", {"suche": suchbegriffe, "treffer": [{"pfad": t["pfad"], "typ": t["typ"]} for t in treffer]})
        quellen = []
        for t in treffer[:5]:
            try:
                if t["typ"] == "ordner":
                    quellen += self.pc.sammle(t["pfad"], max_dateien=30)
                elif t["endung"] or t["vorschau"]:
                    quellen += self.pc.sammle(t["pfad"])
            except (ValueError, OSError, PermissionError):
                continue
            if sum(len(x) for _, x in quellen) > self.cfg["sparen"].get("vorbereiten_bis_zeichen", 250000):
                break
        return quellen

    # ================================================================ Ausführen
    def _system(self, spez, zusammenfassung, dateien_speichern) -> str:
        teile = [self.cfg.get("system_basis", "")]
        if spez.get("system"):
            teile.append(spez["system"])
        anweisungen = self.arbeitsplatz.anweisungen_fuer(spez.get("familie", spez["anbieter"]), spez["anbieter"] == "cli")
        if anweisungen:
            teile.append(anweisungen)
        if dateien_speichern:
            teile.append("Dateien, die du erstellst, als Codeblock ausgeben, der mit ```datei:relativer/pfad.ext "
                         "beginnt – sie werden im gemeinsamen Arbeitsplatz gespeichert. Was du dir dauerhaft merken "
                         "sollst, in einen ```merken Block schreiben.")
        if zusammenfassung:
            teile.append("[Bisheriges Gespräch, verdichtet]\n" + zusammenfassung)
        return "\n\n".join(t for t in teile if t)

    def _mit_ersatz(self, nr, teil, nachrichten, zusammenfassung, opt, verfuegbar, kontext_zeichen,
                    emit, zaehler, abbruch, streamen):
        """Führt eine Teilaufgabe aus; fällt ein Spezialist aus, übernimmt der nächstgünstige."""
        spez, versucht, fehler = teil["spez"], [], []
        while spez and len(versucht) < 3:
            if abbruch and abbruch.is_set():
                raise Abgebrochen()
            emit("schritt", {"nr": nr, "status": "laeuft", "spezialist": spez["id"], "name": spez["name"]})
            gestreamt = [False]

            def delta(stueck):
                if abbruch and abbruch.is_set():
                    raise Abgebrochen()
                gestreamt[0] = True
                emit("delta", {"text": stueck})

            try:
                a = self.rufe(spez, self._system(spez, zusammenfassung, opt["dateien_speichern"]), nachrichten,
                              "aufgabe", on_delta=delta if streamen else None, zaehler=zaehler, abbruch=abbruch)
                if not a.text.strip():
                    raise anbieter.AnbieterFehler("leere Antwort")
                emit("schritt", {"nr": nr, "status": "fertig", "spezialist": spez["id"], "name": spez["name"],
                                 "tokens": a.eingabe_tokens + a.ausgabe_tokens, "geschaetzt": a.geschaetzt,
                                 "cache": bool(a.roh.get("cache")), "text": None if streamen else a.text})
                return a, spez
            except Abgebrochen:
                raise
            except Exception as e:  # noqa: BLE001
                if abbruch and abbruch.is_set():
                    raise Abgebrochen() from None
                versucht.append(spez["id"])
                fehler.append(f"{spez['name']}: {e}")
                if gestreamt[0]:
                    emit("zuruecksetzen", {})
                ersatz = router.waehle(teil["kategorie"], teil["schwierigkeit"], None, verfuegbar, "sparsam",
                                       kontext_zeichen, ausschliessen=versucht)
                emit("ersatz", {"nr": nr, "von": spez["name"], "fehler": str(e)[:400],
                                "an": ersatz["name"] if ersatz else None})
                spez = ersatz
        raise RuntimeError("Kein Spezialist konnte die Aufgabe erledigen:\n" + "\n".join(fehler))

    def _zusammenfuehren(self, nachricht, ergebnisse, emit, zaehler) -> str:
        teile_md = "\n\n".join(f"## {i}. {t['auftrag']}\n*{spez['name']}*\n\n{a.text.strip()}"
                               for i, (t, a, spez) in enumerate(ergebnisse, 1))
        helfer = self.helfer_spez(max_tokens=4096)
        if helfer and len(teile_md) <= self.cfg["sparen"].get("zusammenfuehren_lokal_bis_zeichen", 8000):
            emit("status", {"text": "Ollama führt die Ergebnisse zusammen …"})
            gestreamt = [False]

            def delta(stueck):
                gestreamt[0] = True
                emit("delta", {"text": stueck})
            try:
                a = self.rufe(helfer, ZUSAMMENFUEHREN_SYSTEM,
                              [{"role": "user", "content": f"Ursprüngliche Anfrage:\n{nachricht}\n\nTeilergebnisse:\n{teile_md}"}],
                              "zusammenfuehren", on_delta=delta, cache=False, zaehler=zaehler)
                if a.text.strip():
                    return a.text
            except Exception:  # noqa: BLE001
                pass
            if gestreamt[0]:
                emit("zuruecksetzen", {})
        emit("delta", {"text": teile_md})
        return teile_md

    def bearbeite(self, nachricht: str, gespraech_id: int | None = None, verlauf: list[dict] | None = None,
                  arbeitsplatz_dateien=(), pc_pfade=(), optionen: dict | None = None, emit=None,
                  abbruch: threading.Event | None = None) -> dict:
        emit = emit or (lambda typ, daten: None)
        sparen = self.cfg["sparen"]
        opt = {"spezialist": "auto", "strategie": sparen.get("strategie", "sparsam"),
               "zerlegen": sparen.get("zerlegen", True), "dateien_speichern": True, "pc_suche": True,
               **{k: v for k, v in (optionen or {}).items() if v is not None}}
        nachricht = (nachricht or "").strip()
        if not nachricht:
            raise ValueError("Leere Aufgabe")
        zaehler = {"lokal": 0, "extern": 0}
        start = time.time()

        # 1) Verlauf
        if gespraech_id:
            g = self.speicher.gespraech(gespraech_id)
            if not g:
                raise ValueError("Gespräch nicht gefunden")
            verlauf = [{"role": n["rolle"], "content": n["inhalt"]} for n in g["nachrichten"]]
            if not verlauf:
                self.speicher.setze_titel(gespraech_id, nachricht.splitlines()[0][:60])
        verlauf = verlauf or []

        # 2) Anhänge (Arbeitsplatz + PC)
        quellen = self._anhaenge_laden(arbeitsplatz_dateien, pc_pfade, emit)
        anhang_namen = [n for n, _ in quellen]
        if gespraech_id:
            self.speicher.nachricht_hinzu(gespraech_id, "user", nachricht,
                                          {"anhaenge": list(arbeitsplatz_dateien or []) + list(pc_pfade or [])})

        verfuegbar = self.verfuegbar()
        if not verfuegbar:
            raise RuntimeError("Kein Spezialist verfügbar. Ollama starten (ollama serve), ein Programm wie "
                               "Claude Code installieren oder einen API-Schlüssel in .env eintragen.")

        # 3) Plan – Ollama entscheidet
        kontext_zeichen = len(nachricht) + sum(len(t) for _, t in quellen)
        if opt["spezialist"] != "auto":
            spez = next((s for s in verfuegbar if s["id"] == opt["spezialist"]), None)
            if not spez:
                raise ValueError(f"Spezialist {opt['spezialist']} ist nicht verfügbar")
            plan = {"begruendung": "von dir ausgewählt", "pc_suche": "", "router": None, "fehler": None,
                    "teilaufgaben": [{"kategorie": "text", "schwierigkeit": 2, "vorschlag": spez["id"],
                                      "auftrag": nachricht[:300], "spez": spez}]}
        else:
            emit("status", {"text": "Ollama plant die Aufgabe …"})
            max_teile = sparen.get("max_teilaufgaben", 4) if opt["zerlegen"] and not quellen else 1
            mit_pc = bool(opt["pc_suche"]) and self.speicher.index_anzahl() > 0
            vorher = next((n["content"] for n in reversed(verlauf) if n["role"] == "assistant"), "")
            plan = router.plane(
                lambda spez, system, msgs, json_schema=None: self.rufe(spez, system, msgs, "router",
                                                                       json_schema=json_schema, cache=False,
                                                                       zaehler=zaehler),
                self.router_spez(), verfuegbar, nachricht, [(Path(n).name, len(t)) for n, t in quellen],
                vorher, max_teile, mit_pc, kontext_zeichen)

            # 4) Ollama sucht passende Dateien/Ordner auf dem PC
            if plan.get("pc_suche"):
                gefunden = self._pc_suchen(plan["pc_suche"], emit)
                if gefunden:
                    quellen += gefunden
                    anhang_namen += [n for n, _ in gefunden]
                    kontext_zeichen += sum(len(t) for _, t in gefunden)
            if quellen and len(plan["teilaufgaben"]) > 1:  # Anhänge nicht mehrfach verschicken
                schwerste = max(plan["teilaufgaben"], key=lambda t: t["schwierigkeit"])
                plan["teilaufgaben"] = [{**schwerste, "auftrag": nachricht[:300]}]
            einzeln = len(plan["teilaufgaben"]) == 1
            for t in plan["teilaufgaben"]:
                t["spez"] = router.waehle(t["kategorie"], t["schwierigkeit"], t["vorschlag"], verfuegbar,
                                          opt["strategie"], kontext_zeichen if einzeln else len(t["auftrag"]) + 2000)

        teile = plan["teilaufgaben"]
        emit("plan", {
            "begruendung": plan.get("begruendung"), "router": plan.get("router"), "fehler": plan.get("fehler"),
            "pc_suche": plan.get("pc_suche"), "anhaenge": anhang_namen,
            "teile": [{"nr": i, "kategorie": t["kategorie"], "schwierigkeit": t["schwierigkeit"], "auftrag": t["auftrag"],
                       "spezialist": t["spez"]["id"], "name": t["spez"]["name"], "anbieter": t["spez"]["anbieter"],
                       "modell": t["spez"].get("modell"), "kosten": t["spez"]["kosten"]}
                      for i, t in enumerate(teile, 1)],
        })

        # 5) Kontext verdichten
        zusammenfassung, letzte = self._verlauf_kontext(verlauf, emit, zaehler)

        # 6) Ausführen
        vorbereitung = {}
        if len(teile) == 1:
            t = teile[0]
            anhang_text, vorbereitung = self._vorbereiten(nachricht, quellen, t["spez"], emit, zaehler, abbruch)
            inhalt = nachricht + (f"\n\n[Anhänge]\n{anhang_text}" if anhang_text else "")
            a, spez = self._mit_ersatz(1, t, letzte + [{"role": "user", "content": inhalt}], zusammenfassung, opt,
                                       verfuegbar, kontext_zeichen, emit, zaehler, abbruch, streamen=True)
            text, genutzt = a.text, [spez]
        else:
            def arbeite(nr_t):
                nr, t = nr_t
                msgs = [{"role": "user", "content": f"{t['auftrag']}\n\n[Gesamtanfrage zur Orientierung]\n{nachricht[:1500]}"}]
                a, spez = self._mit_ersatz(nr, t, msgs, zusammenfassung, opt, verfuegbar, 0, emit, zaehler,
                                           abbruch, streamen=False)
                return t, a, spez
            with ThreadPoolExecutor(max_workers=len(teile)) as pool:
                ergebnisse = list(pool.map(arbeite, enumerate(teile, 1)))
            if abbruch and abbruch.is_set():
                raise Abgebrochen()
            text = self._zusammenfuehren(nachricht, ergebnisse, emit, zaehler)
            genutzt = [spez for _, _, spez in ergebnisse]

        # 7) Dateien speichern / merken
        gespeichert, fehler = ([], [])
        if opt["dateien_speichern"]:
            gespeichert, fehler = self.arbeitsplatz.uebernehme_bloecke(text, genutzt[0].get("familie", "ollama"), self.pc)
            if gespeichert or fehler:
                emit("dateien", {"gespeichert": gespeichert, "fehler": fehler})

        meta = {
            "plan": {"begruendung": plan.get("begruendung"), "router": plan.get("router"), "fehler": plan.get("fehler"),
                     "pc_suche": plan.get("pc_suche"),
                     "teile": [{"auftrag": t["auftrag"], "kategorie": t["kategorie"], "schwierigkeit": t["schwierigkeit"]}
                               for t in teile]},
            "spezialisten": [{"id": s["id"], "name": s["name"], "anbieter": s["anbieter"], "modell": s.get("modell")}
                             for s in genutzt],
            "tokens": zaehler, "dateien": gespeichert, "anhaenge": anhang_namen, "vorbereitung": vorbereitung,
            "dauer": round(time.time() - start, 1),
        }
        if gespraech_id:
            self.speicher.nachricht_hinzu(gespraech_id, "assistant", text, meta)
        emit("fertig", {"text": text, "meta": meta})
        return {"text": text, "meta": meta}


def erstelle(cfg=None, **kw) -> Dirigent:
    from .config import lade_config
    return Dirigent(cfg or lade_config(os.environ.get("DIRIGENT_CONFIG")), **kw)
