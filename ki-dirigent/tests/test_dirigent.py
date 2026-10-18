"""Tests ohne echte KI: python -m unittest discover -s tests"""
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dirigent import anbieter, router  # noqa: E402
from dirigent.anbieter import Antwort, AnbieterFehler  # noqa: E402
from dirigent.arbeitsplatz import Arbeitsplatz  # noqa: E402
from dirigent.config import lade_config  # noqa: E402
from dirigent.dirigent import Dirigent  # noqa: E402
from dirigent.server import starte  # noqa: E402


def test_config(tmp: Path, **pc) -> dict:
    cfg = lade_config(str(Path(__file__).resolve().parent.parent / "config.json"))
    cfg["arbeitsplatz_pfad"] = tmp / "ap"
    cfg["daten_pfad"] = tmp / "daten"
    cfg["daten_pfad"].mkdir(parents=True, exist_ok=True)
    cfg["pc"].update({"lesen": [str(tmp)], "schreiben": [], **pc})
    # Programme (CLI) in Tests nicht wirklich starten
    cfg["spezialisten"] = [s for s in cfg["spezialisten"] if s["anbieter"] != "cli"]
    return cfg


SPEZ = [
    {"id": "lokal", "name": "L", "anbieter": "ollama", "kategorien": ["chat", "text"], "niveau": 1, "kosten": 0, "max_kontext_zeichen": 20000},
    {"id": "lokal-code", "name": "LC", "anbieter": "ollama", "kategorien": ["code"], "niveau": 1, "kosten": 0, "max_kontext_zeichen": 20000},
    {"id": "haiku", "name": "H", "anbieter": "cli", "kategorien": ["alles"], "niveau": 2, "kosten": 1, "max_kontext_zeichen": 600000},
    {"id": "sonnet", "name": "S", "anbieter": "cli", "kategorien": ["alles"], "niveau": 3, "kosten": 2, "max_kontext_zeichen": 600000},
    {"id": "opus", "name": "O", "anbieter": "cli", "kategorien": ["code"], "niveau": 3, "kosten": 3, "max_kontext_zeichen": 600000},
    {"id": "gflash", "name": "G", "anbieter": "cli", "kategorien": ["langer_kontext", "text"], "niveau": 2, "kosten": 1, "max_kontext_zeichen": 2000000},
]


class RouterWahl(unittest.TestCase):
    def test_einfach_bleibt_lokal(self):
        self.assertEqual(router.waehle("chat", 1, None, SPEZ)["id"], "lokal")
        self.assertEqual(router.waehle("code", 1, None, SPEZ)["id"], "lokal-code")

    def test_schwierigkeit_steigt(self):
        self.assertEqual(router.waehle("code", 2, None, SPEZ)["id"], "haiku")
        self.assertEqual(router.waehle("code", 3, None, SPEZ)["id"], "sonnet")

    def test_vorschlag_bei_gleichstand(self):
        self.assertEqual(router.waehle("text", 2, "gflash", SPEZ)["id"], "gflash")
        self.assertEqual(router.waehle("text", 2, None, SPEZ)["id"], "haiku")

    def test_strategien(self):
        self.assertEqual(router.waehle("code", 1, "opus", SPEZ, "ollama")["id"], "opus")
        self.assertEqual(router.waehle("code", 1, None, SPEZ, "qualitaet")["id"], "opus")

    def test_kontextgroesse(self):
        self.assertEqual(router.waehle("chat", 1, None, SPEZ, kontext_zeichen=50000)["id"], "haiku")
        self.assertEqual(router.waehle("text", 1, None, SPEZ, kontext_zeichen=1_000_000)["id"], "gflash")

    def test_ausschliessen(self):
        self.assertEqual(router.waehle("code", 3, None, SPEZ, ausschliessen=["sonnet"])["id"], "opus")
        self.assertIsNone(router.waehle("code", 3, None, [], ausschliessen=[]))


class RouterPlan(unittest.TestCase):
    def test_kaputtes_json_nutzt_heuristik(self):
        plan = router.plane(lambda *a, **k: Antwort("kein json"), {"modell": "x"}, SPEZ, "def foo(): pass – Fehler?",
                            [], "", 4, False, 10)
        self.assertEqual(plan["teilaufgaben"][0]["kategorie"], "code")
        self.assertIn("Router", plan["fehler"])

    def test_plan_wird_bereinigt(self):
        roh = {"begruendung": "x", "teilaufgaben": [
            {"kategorie": "quatsch", "schwierigkeit": 9, "spezialist": "gibtsnicht", "auftrag": ""}]}
        plan = router.plane(lambda *a, **k: Antwort(json.dumps(roh)), {"modell": "x"}, SPEZ, "Hallo", [], "", 4, False, 5)
        t = plan["teilaufgaben"][0]
        self.assertEqual((t["kategorie"], t["schwierigkeit"], t["vorschlag"]), ("text", 3, None))
        self.assertEqual(t["auftrag"], "Hallo")

    def test_schema_ohne_pc(self):
        s = router.schema(["a"], 1, False)
        self.assertNotIn("pc_suche", s["properties"])
        self.assertEqual(s["properties"]["teilaufgaben"]["maxItems"], 1)


class ArbeitsplatzTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ap = Arbeitsplatz(test_config(self.tmp))

    def test_ki_ordner_angelegt(self):
        for name in ("gemeinsam", "claude", "gemini", "chatgpt", "ollama"):
            self.assertTrue((self.ap.wurzel / name / "ANWEISUNGEN.md").exists(), name)

    def test_kein_ausbruch(self):
        for boese in ("../x.txt", "a/../../x", "..\\..\\x"):
            with self.assertRaises(ValueError):
                self.ap.pfad(boese)
        # führender Schrägstrich bleibt im Arbeitsplatz
        self.assertTrue(self.ap.pfad("/etc/passwd").is_relative_to(self.ap.wurzel))

    def test_bloecke_und_merken(self):
        text = "Hier:\n```datei:web/index.html\n<h1>Hi</h1>\n```\n\n```merken\nLieblingsfarbe: Blau\n```\n"
        gespeichert, fehler = self.ap.uebernehme_bloecke(text, "anthropic")
        self.assertEqual(fehler, [])
        self.assertIn("web/index.html", gespeichert)
        self.assertEqual(self.ap.lese("web/index.html"), "<h1>Hi</h1>")
        self.assertIn("Lieblingsfarbe", (self.ap.wurzel / "claude" / "GEDAECHTNIS.md").read_text(encoding="utf-8"))
        self.assertIn("Lieblingsfarbe", self.ap.anweisungen_fuer("anthropic"))
        self.assertNotIn("Lieblingsfarbe", self.ap.anweisungen_fuer("gemini"))

    def test_absolute_pfade_brauchen_freigabe(self):
        _, fehler = self.ap.uebernehme_bloecke("```datei:/tmp/boese.txt\nx\n```", "openai")
        self.assertTrue(fehler)

    def test_versionen(self):
        self.ap.schreibe("a.txt", "eins")
        self.ap.schreibe("a.txt", "zwei")
        self.assertEqual(len(list(self.ap.versionen.rglob("a.txt"))), 1)

    def test_leere_vorlagen_kosten_nichts(self):
        self.assertEqual(self.ap.anweisungen_fuer("anthropic"), "")


class PCTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "pcdaten" / "Rechnungen").mkdir(parents=True)
        (self.tmp / "pcdaten" / "Rechnungen" / "Rechnung-2025-03.txt").write_text("Betrag 120 EUR", encoding="utf-8")
        (self.tmp / "pcdaten" / ".ssh").mkdir()
        (self.tmp / "pcdaten" / ".ssh" / "id_rsa").write_text("GEHEIM")
        (self.tmp / "pcdaten" / "geheim.env").write_text("X")
        (self.tmp / "pcdaten" / ".env").write_text("API=1")
        self.d = Dirigent(test_config(self.tmp), chat_fn=lambda *a, **k: Antwort("ok"), modelle_fn=lambda c: [])

    def test_sperren(self):
        with self.assertRaises(PermissionError):
            self.d.pc.lese(str(self.tmp / "pcdaten" / ".ssh" / "id_rsa"))
        with self.assertRaises(PermissionError):
            self.d.pc.lese(str(self.tmp / "pcdaten" / ".env"))
        with self.assertRaises(PermissionError):
            self.d.pc.lese("/etc/hostname")  # nicht freigegeben

    def test_sammeln_ueberspringt_geheimes(self):
        namen = [Path(n).name for n, _ in self.d.pc.sammle(str(self.tmp / "pcdaten"))]
        self.assertIn("Rechnung-2025-03.txt", namen)
        self.assertNotIn("id_rsa", namen)
        self.assertNotIn(".env", namen)

    def test_scan_und_suche(self):
        self.d.pc._scan(self.tmp / "pcdaten")
        treffer = self.d.pc.suche("meine Rechnung 2025")
        self.assertEqual(Path(treffer[0]["pfad"]).name, "Rechnung-2025-03.txt")
        ordner = self.d.pc.suche("Rechnungen", nur_typ="ordner")
        self.assertEqual(Path(ordner[0]["pfad"]).name, "Rechnungen")
        self.assertFalse(any(".ssh" in t["pfad"] for t in self.d.pc.suche("id_rsa ssh")))

    def test_bereitstellen(self):
        ziel = self.d.pc.bereitstellen(str(self.tmp / "pcdaten"), self.d.arbeitsplatz)
        self.assertTrue((self.d.arbeitsplatz.wurzel / ziel / "Rechnungen" / "Rechnung-2025-03.txt").exists())
        self.assertFalse((self.d.arbeitsplatz.wurzel / ziel / ".ssh").exists())


# ---------------------------------------------------------------- Anbieter-Protokolle gegen Fake-Server
class _Fake(BaseHTTPRequestHandler):
    antworten = {}
    letzte = {}

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Fake.letzte[self.path.split("?")[0]] = ({k.lower(): v for k, v in self.headers.items()}, body)
        inhalt = _Fake.antworten[self.path.split("?")[0]](body)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(inhalt.encode())


class AnbieterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Fake)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        basis = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.cfg = {"ollama": {"url": basis}, "anthropic": {"base_url": basis}, "openai": {"base_url": basis + "/v1"},
                   "gemini": {"base_url": basis + "/v1beta"}}
        os.environ.update(ANTHROPIC_API_KEY="a", OPENAI_API_KEY="o", GEMINI_API_KEY="g")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def _stream(self, anb, modell):
        stuecke = []
        a = anbieter.chat(self.cfg, {"anbieter": anb, "modell": modell, "max_tokens": 50},
                          "sys", [{"role": "user", "content": "hi"}], on_delta=stuecke.append)
        return a, "".join(stuecke)

    def test_ollama_stream_ohne_denken(self):
        _Fake.antworten["/api/chat"] = lambda b: "\n".join(json.dumps(x) for x in [
            {"message": {"content": "<thi"}}, {"message": {"content": "nk>geheim</think>Hal"}},
            {"message": {"content": "lo"}, "done": True, "prompt_eval_count": 7, "eval_count": 3}])
        a, gestreamt = self._stream("ollama", "m")
        self.assertEqual((a.text, gestreamt, a.eingabe_tokens, a.ausgabe_tokens), ("Hallo", "Hallo", 7, 3))
        self.assertEqual(_Fake.letzte["/api/chat"][1]["messages"][0], {"role": "system", "content": "sys"})

    def test_anthropic_stream(self):
        ev = [{"type": "message_start", "message": {"usage": {"input_tokens": 11}}},
              {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hal"}},
              {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "lo"}},
              {"type": "message_delta", "usage": {"output_tokens": 2}}]
        _Fake.antworten["/v1/messages"] = lambda b: "".join(f"event: x\ndata: {json.dumps(e)}\n\n" for e in ev)
        a, gestreamt = self._stream("anthropic", "claude-x")
        self.assertEqual((a.text, a.eingabe_tokens, a.ausgabe_tokens), ("Hallo", 11, 2))
        kopf, body = _Fake.letzte["/v1/messages"]
        self.assertEqual(kopf.get("x-api-key"), "a")
        self.assertEqual(body["system"], "sys")

    def test_openai_stream(self):
        ev = [{"choices": [{"delta": {"content": "Hal"}}]}, {"choices": [{"delta": {"content": "lo"}}]},
              {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 2}}]
        _Fake.antworten["/v1/chat/completions"] = lambda b: "".join(f"data: {json.dumps(e)}\n\n" for e in ev) + "data: [DONE]\n\n"
        a, _ = self._stream("openai", "gpt-x")
        self.assertEqual((a.text, a.eingabe_tokens, a.ausgabe_tokens), ("Hallo", 5, 2))
        self.assertEqual(_Fake.letzte["/v1/chat/completions"][1]["max_completion_tokens"], 50)

    def test_gemini_stream(self):
        ev = [{"candidates": [{"content": {"parts": [{"text": "denk", "thought": True}, {"text": "Hal"}]}}]},
              {"candidates": [{"content": {"parts": [{"text": "lo"}]}}],
               "usageMetadata": {"promptTokenCount": 4, "candidatesTokenCount": 2, "thoughtsTokenCount": 1}}]
        _Fake.antworten["/v1beta/models/gem:streamGenerateContent"] = lambda b: "".join(f"data: {json.dumps(e)}\n\n" for e in ev)
        a, _ = self._stream("gemini", "gem")
        self.assertEqual((a.text, a.eingabe_tokens, a.ausgabe_tokens), ("Hallo", 4, 3))
        body = _Fake.letzte["/v1beta/models/gem:streamGenerateContent"][1]
        self.assertEqual(body["systemInstruction"]["parts"][0]["text"], "sys")

    def test_normalisieren(self):
        n = anbieter.normalisiere([{"role": "assistant", "content": "a"}, {"role": "user", "content": "b"},
                                   {"role": "user", "content": "c"}, {"role": "system", "content": "d"}])
        self.assertEqual(n, [{"role": "user", "content": "b\n\nc\n\nd"}])


class CliTest(unittest.TestCase):
    def test_cli_json_und_umgebung(self):
        tmp = Path(tempfile.mkdtemp())
        skript = tmp / "fakeclaude"
        skript.write_text("#!" + sys.executable + "\nimport sys, json, os\np = sys.stdin.read()\n"
                          "print(json.dumps({'result': 'OK ' + str('ANTHROPIC_API_KEY' in os.environ) + ' ' + str('[Aufgabe]' in p),"
                          " 'usage': {'input_tokens': 9, 'output_tokens': 4}}))\n", encoding="utf-8")
        skript.chmod(skript.stat().st_mode | stat.S_IEXEC)
        os.environ["ANTHROPIC_API_KEY"] = "sollte-weg-sein"
        spez = {"anbieter": "cli", "befehl": [str(skript)], "env_entfernen": ["ANTHROPIC_API_KEY"],
                "ausgabe": {"format": "json", "text": "result", "ein": "usage.input_tokens", "aus": "usage.output_tokens"}}
        a = anbieter.chat({}, spez, "sys", [{"role": "user", "content": "Tu was"}], arbeitsordner=tmp)
        self.assertEqual((a.text, a.eingabe_tokens, a.ausgabe_tokens, a.geschaetzt), ("OK False True", 9, 4, False))

    def test_cli_fehlt(self):
        with self.assertRaises(AnbieterFehler):
            anbieter.chat({}, {"anbieter": "cli", "befehl": ["gibt-es-nicht-xyz"]}, "", [{"role": "user", "content": "x"}])


# ---------------------------------------------------------------- Dirigent & Server
def fake_chat_fabrik(plan, fehlschlag=()):
    aufrufe = []

    def chat(cfg, spez, system, nachrichten, on_delta=None, json_schema=None, **kw):
        aufrufe.append((spez["id"], system, nachrichten))
        if json_schema:
            return Antwort(json.dumps(plan), 30, 10, "ollama", spez["modell"])
        if spez["id"] in fehlschlag:
            raise AnbieterFehler("kaputt")
        text = f"Antwort von {spez['id']}"
        if on_delta:
            on_delta(text)
        return Antwort(text, 100, 20, spez["anbieter"], spez.get("modell", ""))
    return chat, aufrufe


class DirigentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        os.environ.update(ANTHROPIC_API_KEY="a", OPENAI_API_KEY="", GEMINI_API_KEY="")
        self.cfg = test_config(self.tmp)

    def neu(self, plan, fehlschlag=()):
        chat, aufrufe = fake_chat_fabrik(plan, fehlschlag)
        return Dirigent(self.cfg, chat_fn=chat, modelle_fn=lambda c: ["qwen2.5:7b", "qwen2.5-coder:7b"]), aufrufe

    def test_schwer_geht_an_claude_und_ersatz(self):
        plan = {"begruendung": "", "teilaufgaben": [{"kategorie": "code", "schwierigkeit": 3, "spezialist": "lokal", "auftrag": "x"}]}
        d, aufrufe = self.neu(plan, fehlschlag=("claude-sonnet-api",))
        ev = []
        r = d.bearbeite("Komplexer Code", emit=lambda t, x: ev.append((t, x)))
        self.assertEqual(r["text"], "Antwort von claude-opus-api")
        self.assertIn("ersatz", [t for t, _ in ev])

    def test_cache_spart_zweiten_aufruf(self):
        plan = {"begruendung": "", "teilaufgaben": [{"kategorie": "text", "schwierigkeit": 2, "spezialist": "lokal", "auftrag": "x"}]}
        d, aufrufe = self.neu(plan)
        d.bearbeite("Gleiche Frage")
        n = len([a for a in aufrufe if a[0] == "claude-haiku-api"])
        r = d.bearbeite("Gleiche Frage")
        self.assertEqual(len([a for a in aufrufe if a[0] == "claude-haiku-api"]), n)
        self.assertEqual(r["meta"]["tokens"].get("cache_treffer"), 1)

    def test_zerlegen_und_lokal_zusammenfuehren(self):
        plan = {"begruendung": "", "teilaufgaben": [
            {"kategorie": "chat", "schwierigkeit": 1, "spezialist": "lokal", "auftrag": "A"},
            {"kategorie": "code", "schwierigkeit": 1, "spezialist": "lokal-code", "auftrag": "B"}]}
        d, aufrufe = self.neu(plan)
        r = d.bearbeite("A und B")
        self.assertEqual([s["id"] for s in r["meta"]["spezialisten"]], ["lokal", "lokal-code"])
        self.assertEqual(aufrufe[-1][0], "helfer")  # Ollama führt zusammen
        self.assertEqual(r["meta"]["tokens"].get("extern", 0), 0)

    def test_verlauf_wird_verdichtet(self):
        plan = {"begruendung": "", "teilaufgaben": [{"kategorie": "text", "schwierigkeit": 2, "spezialist": "lokal", "auftrag": "x"}]}
        d, aufrufe = self.neu(plan)
        verlauf = []
        for i in range(10):
            verlauf += [{"role": "user", "content": f"Frage {i} " + "x" * 800}, {"role": "assistant", "content": "y" * 800}]
        d.bearbeite("Und jetzt?", verlauf=verlauf)
        self.assertTrue(any(a[0] == "helfer" for a in aufrufe))
        haupt = [a for a in aufrufe if a[0] == "claude-haiku-api"][-1]
        self.assertIn("verdichtet", haupt[1])
        self.assertLess(len(haupt[2]), len(verlauf))

    def test_grosse_anhaenge_werden_lokal_vorbereitet(self):
        (self.tmp / "gross.txt").write_text("Unwichtig. " * 5000 + "WICHTIG: Termin am 5. Mai.", encoding="utf-8")
        plan = {"begruendung": "", "teilaufgaben": [{"kategorie": "text", "schwierigkeit": 2, "spezialist": "lokal", "auftrag": "x"}]}
        d, aufrufe = self.neu(plan)
        r = d.bearbeite("Wann ist der Termin?", pc_pfade=[str(self.tmp / "gross.txt")])
        self.assertGreater(r["meta"]["vorbereitung"]["vorher_zeichen"], r["meta"]["vorbereitung"]["nachher_zeichen"])
        haupt = [a for a in aufrufe if a[0] == "claude-haiku-api"][-1]
        self.assertLess(len(haupt[2][-1]["content"]), 20000)

    def test_ohne_ollama_regeln(self):
        chat, _ = fake_chat_fabrik({})
        d = Dirigent(self.cfg, chat_fn=chat, modelle_fn=lambda c: (_ for _ in ()).throw(OSError("aus")))
        r = d.bearbeite("Hallo")
        self.assertEqual(r["meta"]["spezialisten"][0]["anbieter"], "anthropic")
        self.assertIn("Regeln", r["meta"]["plan"]["fehler"])


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = Path(tempfile.mkdtemp())
        os.environ.update(ANTHROPIC_API_KEY="a")
        plan = {"begruendung": "", "teilaufgaben": [{"kategorie": "chat", "schwierigkeit": 1, "spezialist": "lokal", "auftrag": "x"}]}
        chat, _ = fake_chat_fabrik(plan)
        d = Dirigent(test_config(tmp), chat_fn=chat, modelle_fn=lambda c: ["qwen2.5:7b"])
        cls.server = starte(d, "127.0.0.1", 0)
        cls.basis = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def anfrage(self, pfad, body=None, kopf=None):
        req = urllib.request.Request(self.basis + pfad, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json", **(kopf or {})})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_oberflaeche(self):
        status, html = self.anfrage("/")
        self.assertEqual(status, 200)
        self.assertIn("Dirigent", html)
        self.assertEqual(self.anfrage("/ui/../dirigent/server.py")[0], 404)

    def test_aufgabe_stream(self):
        gid = json.loads(self.anfrage("/api/gespraeche", {})[1])["id"]
        status, text = self.anfrage("/api/aufgabe", {"nachricht": "Hallo", "gespraech_id": gid})
        self.assertEqual(status, 200)
        self.assertIn("event: plan", text)
        self.assertIn("event: fertig", text)
        g = json.loads(self.anfrage(f"/api/gespraeche/{gid}")[1])
        self.assertEqual([n["rolle"] for n in g["nachrichten"]], ["user", "assistant"])

    def test_fremde_webseite_blockiert(self):
        self.assertEqual(self.anfrage("/api/gespraeche", {}, {"Origin": "https://boese.example"})[0], 403)
        self.assertEqual(self.anfrage("/api/status", kopf={"Host": "boese.example"})[0], 403)

    def test_ollama_kompatibel(self):
        status, text = self.anfrage("/ollama/api/chat", {"model": "dirigent", "stream": False,
                                                         "messages": [{"role": "user", "content": "Hi"}]})
        self.assertEqual(status, 200)
        obj = json.loads(text)
        self.assertTrue(obj["done"])
        self.assertIn("Antwort von lokal", obj["message"]["content"])
        status, text = self.anfrage("/ollama/api/chat", {"model": "dirigent", "stream": True,
                                                         "messages": [{"role": "user", "content": "Hi"}]})
        zeilen = [json.loads(z) for z in text.strip().splitlines()]
        self.assertTrue(zeilen[-1]["done"])
        self.assertIn("Antwort von lokal", "".join(z["message"]["content"] for z in zeilen))


class ErsatzmodellTest(unittest.TestCase):
    def test_auswahl(self):
        from dirigent.dirigent import ersatzmodell
        installiert = ["nomic-embed-text:latest", "deepseek-r1:8b", "qwen2.5-coder:7b", "llama3.2:latest"]
        self.assertEqual(ersatzmodell(installiert, "allgemein"), "llama3.2:latest")
        self.assertEqual(ersatzmodell(installiert, "code"), "qwen2.5-coder:7b")
        self.assertIsNone(ersatzmodell(["nomic-embed-text:latest"]))

    def test_lokale_spezialisten_nutzen_installierte_modelle(self):
        tmp = Path(tempfile.mkdtemp())
        modelle = ["llama3.2:latest", "qwen2.5-coder:7b", "nomic-embed-text:latest"]
        d = Dirigent(test_config(tmp), chat_fn=lambda *a, **k: Antwort("ok"), modelle_fn=lambda c: modelle)
        lokal = {s["id"]: s["modell"] for s in d.verfuegbar() if s["anbieter"] == "ollama"}
        self.assertEqual(lokal, {"lokal": "llama3.2:latest", "lokal-code": "qwen2.5-coder:7b"})
        self.assertEqual(d.router_spez()["modell"], "llama3.2:latest")
        bereit, grund = d.spezialist_status(next(s for s in d.cfg["spezialisten"] if s["id"] == "lokal"))
        self.assertTrue(bereit)
        self.assertIn("Ersatzmodell", grund)


class ProgrammeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = test_config(self.tmp)
        self.laufwerk = self.tmp / "D" / "Tools" / "Ollama"
        self.laufwerk.mkdir(parents=True)
        self.exe = self.laufwerk / "ollama"
        self.exe.write_text("#!/bin/sh\nexit 0\n")
        self.exe.chmod(self.exe.stat().st_mode | stat.S_IEXEC)

    def test_eigene_pfade(self):
        from dirigent.programme import Programme
        self.cfg["programme"] = {"pfade": [str(self.laufwerk)]}
        p = Programme(self.cfg)
        self.assertEqual(p.finde("ollama"), str(self.exe))
        self.assertIn(str(self.laufwerk), p.umgebung({"PATH": "/usr/bin"})["PATH"])
        self.assertIsNone(p.finde("gibt-es-nicht-xyz"))

    def test_laufwerkssuche_merkt_sich_fundort(self):
        from dirigent.programme import Programme
        p = Programme(self.cfg)
        p.suchen(zeitlimit=10, wurzeln=[self.tmp / "D"])
        self.assertEqual(p.gefunden.get("ollama"), str(self.exe))
        self.assertEqual(Programme(self.cfg).finde("ollama"), str(self.exe))  # aus programme.json

    def test_cli_spezialist_wird_ueber_suche_gefunden(self):
        (self.laufwerk / "gemini").write_text("#!/bin/sh\n")
        (self.laufwerk / "gemini").chmod(0o755)
        self.cfg["programme"] = {"pfade": [str(self.laufwerk)]}
        self.cfg["spezialisten"].append({"id": "g", "name": "G", "anbieter": "cli", "befehl": ["gemini"],
                                         "kategorien": ["alles"], "niveau": 2, "kosten": 1, "max_tokens": 1,
                                         "max_kontext_zeichen": 1, "aktiv": "auto", "extra": {}, "staerken": ""})
        d = Dirigent(self.cfg, chat_fn=lambda *a, **k: Antwort("ok"), modelle_fn=lambda c: [])
        self.assertTrue(d.spezialist_status(self.cfg["spezialisten"][-1])[0])

    def test_autostart_ohne_programm(self):
        from dirigent.programme import Programme
        self.cfg["ollama"]["url"] = "http://127.0.0.1:1"
        self.cfg["ollama"]["programm"] = str(self.tmp / "fehlt" / "ollama.exe")
        ok, meldung = Programme(self.cfg).ollama_starten(warten=1)
        self.assertFalse(ok)
        self.assertIn("nicht gefunden", meldung)
        self.cfg["ollama"]["autostart"] = False
        self.assertIn("ausgeschaltet", Programme(self.cfg).ollama_starten()[1])


class AktualisierenTest(unittest.TestCase):
    def test_update_aus_zip(self):
        import zipfile
        from dirigent import aktualisieren as akt
        tmp = Path(tempfile.mkdtemp())
        basis = tmp / "ki-dirigent"
        (basis / "dirigent").mkdir(parents=True)
        (basis / "arbeitsplatz").mkdir()
        (basis / "dirigent" / "alt.py").write_text("alt")
        (basis / "arbeitsplatz" / "meins.txt").write_text("bleibt")
        (basis / ".env").write_text("KEY=geheim")
        (basis / "config.json").write_text(json.dumps({"ollama": {"programm": "D:\\Ollama\\ollama.exe", "url": "u"},
                                                       "spezialisten": [1]}))
        (basis / "daten").mkdir()
        (basis / "daten" / akt.AUSGELIEFERT).write_text(json.dumps({"ollama": {"programm": "", "url": "u"},
                                                                    "spezialisten": [1]}))
        zipdatei = tmp / "neu.zip"
        with zipfile.ZipFile(zipdatei, "w") as zf:
            zf.writestr("Repo-zweig/index.html", "Website")
            zf.writestr("Repo-zweig/ki-dirigent/dirigent/neu.py", "neu")
            zf.writestr("Repo-zweig/ki-dirigent/start.bat", "start")
            zf.writestr("Repo-zweig/ki-dirigent/arbeitsplatz/fremd.txt", "darf nicht kommen")
            zf.writestr("Repo-zweig/ki-dirigent/config.json", json.dumps(
                {"ollama": {"programm": "", "url": "u", "autostart": True}, "spezialisten": [1, 2]}))
        ergebnis = akt.aktualisieren(str(zipdatei), basis)
        self.assertFalse((basis / "dirigent" / "alt.py").exists())
        self.assertEqual((basis / "dirigent" / "neu.py").read_text(), "neu")
        self.assertEqual((basis / "arbeitsplatz" / "meins.txt").read_text(), "bleibt")
        self.assertFalse((basis / "arbeitsplatz" / "fremd.txt").exists())
        self.assertEqual((basis / ".env").read_text(), "KEY=geheim")
        self.assertFalse((basis / "index.html").exists())
        cfg = json.loads((basis / "config.json").read_text())
        self.assertEqual(cfg["ollama"]["programm"], "D:\\Ollama\\ollama.exe")  # eigene Änderung bleibt
        self.assertTrue(cfg["ollama"]["autostart"])  # neue Einstellung kommt dazu
        self.assertEqual(cfg["spezialisten"], [1, 2])  # unverändertes wird aktualisiert
        self.assertTrue((basis / "config.vorher.json").exists())
        self.assertEqual(ergebnis["kopiert"], 2)  # neu.py + start.bat (config.json wird zusammengeführt)


if __name__ == "__main__":
    unittest.main()
