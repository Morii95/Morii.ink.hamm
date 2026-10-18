"""Lokaler Webserver: Oberfläche, API und Ollama-kompatible Schnittstelle (/ollama/api/…)."""
import base64
import json
import mimetypes
import select
import socket
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import anbieter
from .dirigent import Abgebrochen, Dirigent

UI = Path(__file__).resolve().parent.parent / "ui"
MAX_BODY = 60 * 1024 * 1024
KOMPAT_MODELL = "dirigent:latest"


class HTTPFehler(Exception):
    def __init__(self, status, text):
        super().__init__(text)
        self.status = status


def _jetzt():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class Handler(BaseHTTPRequestHandler):
    server_version = "Dirigent/1.0"
    dirigent: Dirigent = None  # wird in starte() gesetzt

    # ---------------------------------------------------------- Grundlagen
    def log_message(self, fmt, *args):
        pass

    def _host_ok(self) -> bool:
        erlaubt = self.dirigent.cfg["server"].get("erlaubte_hosts", ["localhost", "127.0.0.1", "::1"])
        if "*" in erlaubt:
            return True
        host = (self.headers.get("Host") or "").strip()
        if host.startswith("["):
            host = host[1:host.find("]")]
        elif host.count(":") == 1:
            host = host.split(":")[0]
        return host in erlaubt

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        return urlsplit(origin).netloc == (self.headers.get("Host") or "")

    def _json(self, status, obj):
        daten = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(daten)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(daten)

    def _roh_body(self) -> bytes:
        laenge = int(self.headers.get("Content-Length") or 0)
        if laenge > MAX_BODY:
            raise HTTPFehler(413, "Anfrage zu groß")
        return self.rfile.read(laenge) if laenge else b""

    def _body(self) -> dict:
        roh = self._roh_body()
        if not roh:
            return {}
        try:
            return json.loads(roh)
        except json.JSONDecodeError:
            raise HTTPFehler(400, "Ungültiges JSON") from None

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_PUT(self):
        self._route("PUT")

    def do_DELETE(self):
        self._route("DELETE")

    def do_HEAD(self):
        self._route("HEAD")

    def _route(self, methode):
        teile = urlsplit(self.path)
        pfad, q = teile.path, {k: v[0] for k, v in parse_qs(teile.query).items()}
        try:
            if not self._host_ok():
                raise HTTPFehler(403, "Host nicht erlaubt")
            if methode not in ("GET", "HEAD") and not self._origin_ok():
                raise HTTPFehler(403, "Fremde Webseite – Anfrage blockiert")
            if pfad.startswith("/ollama/") or pfad == "/ollama":
                return self._kompat(methode, pfad[len("/ollama"):] or "/", teile.query)
            if not pfad.startswith("/api/"):
                return self._statisch(pfad, methode)
            if methode == "POST" and pfad == "/api/aufgabe":
                return self._aufgabe(self._body())
            ergebnis = self._api(methode, pfad, q)
            self._json(200, ergebnis)
        except HTTPFehler as e:
            self._json(e.status, {"fehler": str(e)})
        except PermissionError as e:
            self._json(403, {"fehler": str(e)})
        except (FileNotFoundError, NotADirectoryError) as e:
            self._json(404, {"fehler": f"nicht gefunden: {e}"})
        except (ValueError, RuntimeError, anbieter.AnbieterFehler) as e:
            self._json(400, {"fehler": str(e)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001
            try:
                self._json(500, {"fehler": f"{type(e).__name__}: {e}"})
            except OSError:
                pass

    # ---------------------------------------------------------- Oberfläche
    def _statisch(self, pfad, methode):
        if methode not in ("GET", "HEAD"):
            raise HTTPFehler(405, "Methode nicht erlaubt")
        name = "index.html" if pfad in ("/", "") else pfad.lstrip("/").removeprefix("ui/")
        datei = (UI / name).resolve()
        if not datei.is_relative_to(UI) or not datei.is_file():
            raise HTTPFehler(404, "nicht gefunden")
        daten = datei.read_bytes()
        typ = mimetypes.guess_type(datei.name)[0] or "application/octet-stream"
        if typ.startswith("text/") or typ in ("application/javascript",):
            typ += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", typ)
        self.send_header("Content-Length", str(len(daten)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        if methode == "GET":
            self.wfile.write(daten)

    # ---------------------------------------------------------- JSON-API
    def _api(self, methode, pfad, q):
        d = self.dirigent
        sp, ap, pc = d.speicher, d.arbeitsplatz, d.pc
        route = (methode, pfad)

        if route == ("GET", "/api/status"):
            return d.status()
        if route == ("POST", "/api/verbindung"):
            a = self._body().get("anbieter", "")
            return {"anbieter": a, "modelle": anbieter.modelle_auflisten(d.cfg, a)}
        if route == ("POST", "/api/programme/suchen"):
            return d.programme_suchen(zeitlimit=float(self._body().get("zeitlimit", 30)))
        if route == ("GET", "/api/verbrauch"):
            return sp.verbrauch_statistik()

        # Gespräche
        if route == ("GET", "/api/gespraeche"):
            return {"gespraeche": sp.gespraeche()}
        if route == ("POST", "/api/gespraeche"):
            return {"id": sp.neues_gespraech(self._body().get("titel") or "Neue Aufgabe")}
        if pfad.startswith("/api/gespraeche/"):
            try:
                gid = int(pfad.rsplit("/", 1)[1])
            except ValueError:
                raise HTTPFehler(400, "ungültige ID") from None
            if methode == "GET":
                g = sp.gespraech(gid)
                if not g:
                    raise HTTPFehler(404, "Gespräch nicht gefunden")
                return g
            if methode == "DELETE":
                sp.loesche_gespraech(gid)
                return {"ok": True}

        # Arbeitsplatz + KI-Ordner
        if route == ("GET", "/api/arbeitsplatz"):
            return {"wurzel": str(ap.wurzel), "dateien": ap.liste(), "ki_ordner": ap.ki_ordner()}
        if pfad == "/api/arbeitsplatz/datei":
            if methode == "GET":
                return {"pfad": q.get("pfad"), "inhalt": ap.lese(q.get("pfad", ""), 400_000)}
            if methode == "PUT":
                b = self._body()
                inhalt = base64.b64decode(b["base64"]) if "base64" in b else str(b.get("inhalt", ""))
                return {"gespeichert": ap.schreibe(b.get("pfad", ""), inhalt)}
            if methode == "DELETE":
                ap.loesche(q.get("pfad", ""))
                return {"ok": True}

        # PC
        if route == ("GET", "/api/pc/start"):
            return {"startpunkte": pc.startpunkte(), "lesen": [str(p) for p in pc.lese_wurzeln],
                    "schreiben": [str(p) for p in pc.schreib_wurzeln]}
        if route == ("GET", "/api/pc/ordner"):
            return pc.ordner(q.get("pfad") or str(Path.home()))
        if route == ("GET", "/api/pc/datei"):
            return {"pfad": q.get("pfad"), "inhalt": pc.lese(q.get("pfad", ""), 200_000)}
        if route == ("GET", "/api/pc/suche"):
            return {"treffer": pc.suche(q.get("q", ""), limit=int(q.get("limit", 30)), nur_typ=q.get("typ") or None)}
        if route == ("GET", "/api/pc/scan"):
            return pc.job_status()
        if route == ("POST", "/api/pc/scan"):
            b = self._body()
            job = pc.analyse_starten(b["pfad"]) if b.get("analyse") else pc.scan_starten(b["pfad"])
            return {k: v for k, v in job.items() if k != "abbrechen"}
        if route == ("POST", "/api/pc/scan/abbrechen"):
            pc.abbrechen()
            return {"ok": True}
        if route == ("POST", "/api/pc/bereitstellen"):
            return {"ziel": pc.bereitstellen(self._body().get("pfad", ""), ap)}

        raise HTTPFehler(404, f"Unbekannter Endpunkt {methode} {pfad}")

    # ---------------------------------------------------------- Aufgabe (Server-Sent Events)
    def _verbindung_beobachten(self, abbruch: threading.Event, fertig: threading.Event):
        """Merkt, wenn der Browser die Verbindung schließt (Stopp-Knopf) → laufende KI-Aufrufe abbrechen."""
        sock = self.connection
        while not fertig.is_set():
            try:
                lesbar, _, _ = select.select([sock], [], [], 0.5)
                if lesbar and not sock.recv(1, socket.MSG_PEEK):
                    abbruch.set()
                    return
            except (OSError, ValueError):
                abbruch.set()
                return

    def _aufgabe(self, b: dict):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        abbruch, fertig, lock = threading.Event(), threading.Event(), threading.Lock()
        threading.Thread(target=self._verbindung_beobachten, args=(abbruch, fertig), daemon=True).start()

        def emit(typ, daten):
            if abbruch.is_set():
                raise Abgebrochen()
            try:
                with lock:
                    self.wfile.write(f"event: {typ}\ndata: {json.dumps(daten, ensure_ascii=False)}\n\n".encode("utf-8"))
                    self.wfile.flush()
            except OSError:
                abbruch.set()
                raise Abgebrochen() from None

        try:
            self.dirigent.bearbeite(
                b.get("nachricht", ""), gespraech_id=b.get("gespraech_id"),
                arbeitsplatz_dateien=b.get("arbeitsplatz_dateien") or [], pc_pfade=b.get("pc_pfade") or [],
                optionen=b.get("optionen") or {}, emit=emit, abbruch=abbruch)
        except Abgebrochen:
            pass
        except Exception as e:  # noqa: BLE001
            try:
                emit("fehler", {"text": str(e)})
            except Abgebrochen:
                pass
        finally:
            fertig.set()

    # ---------------------------------------------------------- Ollama-kompatibel
    def _kompat(self, methode, pfad, query):
        """Andere Ollama-Oberflächen (z. B. Open WebUI) können http://localhost:8765/ollama als
        Ollama-Adresse nutzen: Dort erscheint das Modell 'dirigent', alle anderen Anfragen gehen an Ollama."""
        roh = self._roh_body() if methode in ("POST", "PUT", "DELETE") else b""
        daten = {}
        if roh:
            try:
                daten = json.loads(roh)
            except json.JSONDecodeError:
                daten = {}
        modell = str(daten.get("model") or daten.get("name") or "")
        ist_dirigent = modell.split(":")[0] == "dirigent"

        if methode == "GET" and pfad == "/api/tags":
            try:
                modelle = json.load(urllib.request.urlopen(self.dirigent.cfg["ollama"]["url"].rstrip("/") + "/api/tags",
                                                           timeout=5)).get("models", [])
            except (OSError, ValueError):
                modelle = []
            eintrag = {"name": KOMPAT_MODELL, "model": KOMPAT_MODELL, "modified_at": _jetzt(), "size": 0,
                       "digest": "0" * 64, "details": {"format": "dirigent", "family": "dirigent", "families": ["dirigent"],
                                                       "parameter_size": "verteilt", "quantization_level": "-"}}
            return self._json(200, {"models": [eintrag] + modelle})
        if ist_dirigent and pfad == "/api/show":
            return self._json(200, {"modelfile": "", "parameters": "", "template": "", "capabilities": ["completion"],
                                    "details": {"format": "dirigent", "family": "dirigent", "parameter_size": "verteilt"},
                                    "model_info": {}})
        if ist_dirigent and pfad in ("/api/chat", "/api/generate") and methode == "POST":
            return self._kompat_chat(daten, generate=pfad == "/api/generate")
        return self._weiterleiten(methode, pfad + (f"?{query}" if query else ""), roh)

    def _weiterleiten(self, methode, pfad, roh):
        url = self.dirigent.cfg["ollama"]["url"].rstrip("/") + pfad
        req = urllib.request.Request(url, data=roh or None, method=methode,
                                     headers={"Content-Type": self.headers.get("Content-Type", "application/json")})
        try:
            resp = urllib.request.urlopen(req, timeout=3600)
        except urllib.error.HTTPError as e:
            resp = e
        except urllib.error.URLError as e:
            raise HTTPFehler(502, f"Ollama nicht erreichbar: {e.reason}") from None
        with resp:
            self.send_response(getattr(resp, "status", None) or resp.getcode())
            self.send_header("Content-Type", resp.headers.get("Content-Type", "application/json"))
            self.end_headers()
            lesen = getattr(resp, "read1", resp.read)
            while True:
                stueck = lesen(65536)
                if not stueck:
                    break
                self.wfile.write(stueck)
                self.wfile.flush()

    def _kompat_chat(self, daten, generate: bool):
        if generate:
            msgs = [{"role": "user", "content": daten.get("prompt", "")}]
            if daten.get("system"):
                msgs.insert(0, {"role": "system", "content": daten["system"]})
        else:
            msgs = daten.get("messages") or []
        system = "\n".join(str(m.get("content", "")) for m in msgs if m.get("role") == "system")[:2000]
        rest = [{"role": m["role"], "content": str(m.get("content", ""))} for m in msgs
                if m.get("role") in ("user", "assistant")]
        if not rest or rest[-1]["role"] != "user":
            raise HTTPFehler(400, "Letzte Nachricht muss vom Nutzer sein")
        nachricht = rest[-1]["content"]
        if system:
            nachricht = f"[Vorgabe des Programms]\n{system}\n\n{nachricht}"
        stream = daten.get("stream", True) is not False
        feld = "response" if generate else "message"
        start = time.time()
        zeige_weg = self.dirigent.cfg["sparen"].get("weg_in_kompat_antwort", True)
        abbruch, lock = threading.Event(), threading.Lock()
        gesammelt = []

        def stueck(text, fertig=False, extra=None):
            obj = {"model": KOMPAT_MODELL, "created_at": _jetzt(), "done": fertig}
            obj[feld] = text if generate else {"role": "assistant", "content": text}
            obj.update(extra or {})
            return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")

        def schreibe(text):
            if not text:
                return
            gesammelt.append(text)
            if stream:
                try:
                    with lock:
                        self.wfile.write(stueck(text))
                        self.wfile.flush()
                except OSError:
                    abbruch.set()
                    raise Abgebrochen() from None

        def emit(typ, d):
            if abbruch.is_set():
                raise Abgebrochen()
            if typ == "plan" and zeige_weg:
                namen = " + ".join(dict.fromkeys(t["name"] for t in d["teile"]))
                schreibe(f"*Dirigent → {namen}*\n\n")
            elif typ == "delta":
                schreibe(d["text"])
            elif typ == "zuruecksetzen":
                schreibe("\n\n*(neuer Versuch mit anderem Spezialisten)*\n\n")
            elif typ == "dateien" and d.get("gespeichert"):
                schreibe("\n\n*Gespeichert im Arbeitsplatz: " + ", ".join(d["gespeichert"]) + "*")

        if stream:
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
        tokens = {}
        try:
            ergebnis = self.dirigent.bearbeite(nachricht, verlauf=rest[:-1], emit=emit, abbruch=abbruch)
            tokens = ergebnis["meta"]["tokens"]
        except Abgebrochen:
            return
        except Exception as e:  # noqa: BLE001
            if not stream:
                raise
            schreibe(f"\n\n**Fehler:** {e}")
        abschluss = {"done_reason": "stop", "total_duration": int((time.time() - start) * 1e9),
                     "prompt_eval_count": tokens.get("extern", 0), "eval_count": tokens.get("lokal", 0)}
        if stream:
            try:
                self.wfile.write(stueck("", True, abschluss))
                self.wfile.flush()
            except OSError:
                pass
        else:
            daten = json.loads(stueck("".join(gesammelt), True, abschluss))
            self._json(200, daten)


def starte(dirigent: Dirigent, host=None, port=None) -> ThreadingHTTPServer:
    host = host or dirigent.cfg["server"].get("host", "127.0.0.1")
    port = port if port is not None else dirigent.cfg["server"].get("port", 8765)
    Handler.dirigent = dirigent
    ThreadingHTTPServer.daemon_threads = True
    server = ThreadingHTTPServer((host, port), Handler)
    return server
