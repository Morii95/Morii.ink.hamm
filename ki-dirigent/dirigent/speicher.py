"""SQLite-Speicher: Gespräche, Token-Verbrauch, Antwort-Cache, PC-Index."""
import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS gespraeche (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    titel TEXT NOT NULL,
    erstellt REAL NOT NULL,
    geaendert REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS nachrichten (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gespraech_id INTEGER NOT NULL REFERENCES gespraeche(id) ON DELETE CASCADE,
    rolle TEXT NOT NULL,
    inhalt TEXT NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}',
    zeit REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nachrichten ON nachrichten(gespraech_id, id);
CREATE TABLE IF NOT EXISTS verbrauch (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    zeit REAL NOT NULL,
    anbieter TEXT NOT NULL,
    modell TEXT NOT NULL,
    spezialist TEXT NOT NULL,
    zweck TEXT NOT NULL,
    ein INTEGER NOT NULL,
    aus INTEGER NOT NULL,
    aus_cache INTEGER NOT NULL DEFAULT 0,
    geschaetzt INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS cache (
    schluessel TEXT PRIMARY KEY,
    wert TEXT NOT NULL,
    zeit REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS pc_index (
    pfad TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    ordner TEXT NOT NULL,
    typ TEXT NOT NULL,
    endung TEXT NOT NULL,
    groesse INTEGER NOT NULL,
    geaendert REAL NOT NULL,
    vorschau TEXT NOT NULL DEFAULT '',
    zusammenfassung TEXT NOT NULL DEFAULT '',
    vektor TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_pc_ordner ON pc_index(ordner);
"""


class Speicher:
    def __init__(self, pfad: Path):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(pfad), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.executescript(SCHEMA)

    def _alle(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def _schreibe(self, sql, args=()):
        with self.lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur.lastrowid

    # ---------------------------------------------------------- Gespräche
    def gespraeche(self):
        return self._alle("SELECT id, titel, geaendert FROM gespraeche ORDER BY geaendert DESC LIMIT 300")

    def neues_gespraech(self, titel="Neue Aufgabe") -> int:
        jetzt = time.time()
        return self._schreibe("INSERT INTO gespraeche (titel, erstellt, geaendert) VALUES (?, ?, ?)",
                              (titel, jetzt, jetzt))

    def gespraech(self, gid: int):
        kopf = self._alle("SELECT * FROM gespraeche WHERE id = ?", (gid,))
        if not kopf:
            return None
        nachrichten = self._alle("SELECT id, rolle, inhalt, meta, zeit FROM nachrichten "
                                 "WHERE gespraech_id = ? ORDER BY id", (gid,))
        for n in nachrichten:
            n["meta"] = json.loads(n["meta"] or "{}")
        return {**kopf[0], "nachrichten": nachrichten}

    def setze_titel(self, gid: int, titel: str):
        self._schreibe("UPDATE gespraeche SET titel = ? WHERE id = ?", (titel[:120], gid))

    def loesche_gespraech(self, gid: int):
        self._schreibe("DELETE FROM gespraeche WHERE id = ?", (gid,))

    def nachricht_hinzu(self, gid: int, rolle: str, inhalt: str, meta: dict | None = None):
        jetzt = time.time()
        with self.lock:
            self.db.execute("INSERT INTO nachrichten (gespraech_id, rolle, inhalt, meta, zeit) VALUES (?, ?, ?, ?, ?)",
                            (gid, rolle, inhalt, json.dumps(meta or {}, ensure_ascii=False), jetzt))
            self.db.execute("UPDATE gespraeche SET geaendert = ? WHERE id = ?", (jetzt, gid))
            self.db.commit()

    # ---------------------------------------------------------- Verbrauch
    def verbrauch_hinzu(self, anbieter, modell, spezialist, zweck, ein, aus, aus_cache=False, geschaetzt=False):
        self._schreibe("INSERT INTO verbrauch (zeit, anbieter, modell, spezialist, zweck, ein, aus, aus_cache, geschaetzt) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (time.time(), anbieter, modell, spezialist, zweck, int(ein or 0), int(aus or 0),
                        int(aus_cache), int(geschaetzt)))

    def verbrauch_statistik(self):
        heute = time.time() - (time.time() % 86400)
        zeilen = self._alle(
            "SELECT anbieter, modell, COUNT(*) AS aufrufe, SUM(ein) AS ein, SUM(aus) AS aus, "
            "SUM(aus_cache) AS cache_treffer, MAX(geschaetzt) AS geschaetzt, "
            "SUM(CASE WHEN zeit >= ? THEN ein + aus ELSE 0 END) AS heute "
            "FROM verbrauch GROUP BY anbieter, modell ORDER BY anbieter, modell", (heute,))
        lokal = sum((z["ein"] or 0) + (z["aus"] or 0) for z in zeilen if z["anbieter"] == "ollama")
        gesamt = sum((z["ein"] or 0) + (z["aus"] or 0) for z in zeilen)
        return {"zeilen": zeilen, "lokal_tokens": lokal, "gesamt_tokens": gesamt,
                "lokal_anteil": round(100 * lokal / gesamt, 1) if gesamt else 0}

    # ---------------------------------------------------------- Cache
    def cache_lesen(self, schluessel: str):
        z = self._alle("SELECT wert FROM cache WHERE schluessel = ?", (schluessel,))
        return z[0]["wert"] if z else None

    def cache_schreiben(self, schluessel: str, wert: str):
        self._schreibe("INSERT OR REPLACE INTO cache (schluessel, wert, zeit) VALUES (?, ?, ?)",
                       (schluessel, wert, time.time()))

    # ---------------------------------------------------------- PC-Index
    def index_schreiben(self, eintraege: list[tuple]):
        """eintraege: (pfad, name, ordner, typ, endung, groesse, geaendert, vorschau)"""
        with self.lock:
            self.db.executemany(
                "INSERT INTO pc_index (pfad, name, ordner, typ, endung, groesse, geaendert, vorschau) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(pfad) DO UPDATE SET "
                "groesse = excluded.groesse, geaendert = excluded.geaendert, vorschau = excluded.vorschau, "
                "zusammenfassung = CASE WHEN pc_index.geaendert = excluded.geaendert THEN pc_index.zusammenfassung ELSE '' END, "
                "vektor = CASE WHEN pc_index.geaendert = excluded.geaendert THEN pc_index.vektor ELSE '' END",
                eintraege)
            self.db.commit()

    def index_analyse_setzen(self, pfad: str, zusammenfassung: str, vektor: list[float] | None):
        self._schreibe("UPDATE pc_index SET zusammenfassung = ?, vektor = ? WHERE pfad = ?",
                       (zusammenfassung, json.dumps(vektor) if vektor else "", pfad))

    def index_anzahl(self) -> int:
        return self._alle("SELECT COUNT(*) AS n FROM pc_index")[0]["n"]

    def index_suche_text(self, woerter: list[str], limit=400):
        if not woerter:
            return []
        bedingungen = " OR ".join(["(name LIKE ? OR pfad LIKE ? OR vorschau LIKE ? OR zusammenfassung LIKE ?)"] * len(woerter))
        args = []
        for w in woerter:
            args += [f"%{w}%"] * 4
        return self._alle(f"SELECT pfad, name, ordner, typ, endung, groesse, geaendert, vorschau, zusammenfassung "
                          f"FROM pc_index WHERE {bedingungen} LIMIT ?", (*args, limit))

    def index_mit_vektor(self, limit=20000):
        return self._alle("SELECT pfad, name, ordner, typ, endung, groesse, geaendert, vorschau, zusammenfassung, vektor "
                          "FROM pc_index WHERE vektor != '' LIMIT ?", (limit,))

    def index_unter(self, ordner: str, limit=1000):
        muster = ordner.rstrip("/\\") + "%"
        return self._alle("SELECT pfad, typ, endung, groesse, geaendert, zusammenfassung FROM pc_index "
                          "WHERE pfad LIKE ? AND typ = 'datei' LIMIT ?", (muster, limit))
