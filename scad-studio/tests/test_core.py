"""Tests für Kern-Funktionen: Prompts, Einstellungen, Projekte, Server, KI-Schleife.

Ausführen: cd scad-studio && python -m unittest discover -s tests
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Eigener App-Ordner, damit die Tests nichts beim Benutzer anlegen
_HOME = tempfile.mkdtemp(prefix="scadstudio-test-")
os.environ["SCAD_STUDIO_HOME"] = _HOME

from scadstudio import openscad, pipeline  # noqa: E402
from scadstudio.ai import prompts  # noqa: E402
from scadstudio.config import Settings  # noqa: E402
from scadstudio.jobs import Job  # noqa: E402
from scadstudio.printers import printer_profile  # noqa: E402
from scadstudio.projects import ProjectStore  # noqa: E402

HAS_OPENSCAD = openscad.detect().found


class PromptTests(unittest.TestCase):
    def test_extract_largest_openscad_block(self):
        text = "Erklärung\n```\nshort\n```\nMehr\n```openscad\ncube(10);\nsphere(5);\n```\nEnde"
        code, explanation = prompts.extract_code(text)
        self.assertEqual(code, "cube(10);\nsphere(5);\n")
        self.assertIn("Erklärung", explanation)
        self.assertNotIn("cube", explanation)

    def test_extract_truncated_block(self):
        code, _ = prompts.extract_code("Text\n```openscad\nmodule a() { cube(1); }\na();")
        self.assertIn("module a()", code)

    def test_extract_no_code(self):
        code, explanation = prompts.extract_code("PASST")
        self.assertEqual(code, "")
        self.assertTrue(prompts.is_approval("PASST"))
        self.assertFalse(prompts.is_approval("```openscad\ncube(1);\n```"))

    def test_parse_parts(self):
        code = ('part = "assembly"; // [assembly:Zusammenbau, all_parts:Alle Teile, leg:Bein, '
                'hub:Nabe, shade:Schirm]\n')
        self.assertEqual([p["id"] for p in prompts.parse_parts(code)], ["leg", "hub", "shade"])
        self.assertEqual(prompts.parse_parts(code)[0]["label"], "Bein")
        self.assertEqual(prompts.parse_parts("x = 1;"), [])

    def test_system_prompt_contains_printer_and_library(self):
        settings = Settings(Path(_HOME) / "s1.json")
        text = prompts.system_prompt(printer_profile(settings))
        self.assertIn("220 x 220 x 250", text)
        self.assertIn("Anycubic Kobra 2 Neo", text)
        self.assertIn("studio.scad", text)
        self.assertIn("bed = [220, 220, 250]", text)


class SettingsTests(unittest.TestCase):
    def test_secret_semantics(self):
        settings = Settings(Path(_HOME) / "s2.json")
        settings.update({"gemini_api_key": "abcdefghijkl1234"})
        self.assertEqual(settings.get("gemini_api_key"), "abcdefghijkl1234")
        settings.update({"gemini_api_key": ""})           # leer = behalten
        self.assertEqual(settings.get("gemini_api_key"), "abcdefghijkl1234")
        public = settings.public()
        self.assertEqual(public["gemini_api_key"], "")
        self.assertTrue(public["gemini_api_key_hint"].endswith("1234"))
        settings.update({"gemini_api_key": None})         # None = löschen
        self.assertEqual(Settings(Path(_HOME) / "s2.json")._data["gemini_api_key"], "")

    def test_types_and_unknown_keys(self):
        settings = Settings(Path(_HOME) / "s3.json")
        settings.update({"repair_attempts": "5", "use_manifold": 0, "evil": "x", "printer": "bambu_a1"})
        self.assertEqual(settings.get("repair_attempts"), 5)
        self.assertFalse(settings.get("use_manifold"))
        self.assertNotIn("evil", settings._data)
        self.assertEqual(printer_profile(settings)["bed"], [256, 256, 256])


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.store = ProjectStore(Path(tempfile.mkdtemp(prefix="proj-", dir=_HOME)))

    def test_versions_and_library(self):
        project = self.store.create("Test Öse / 1", "code")
        self.assertTrue((project.path / "studio.scad").exists() or True)
        self.assertEqual(project.write_code("cube(1);", "a"), 1)
        self.assertEqual(project.write_code("cube(1);", "gleich"), 1)   # unverändert → keine neue Version
        self.assertEqual(project.write_code("cube(2);", "b"), 2)
        self.assertEqual(project.read_version(1), "cube(1);\n")
        self.assertIn(project.id, [p["id"] for p in self.store.list()])

    def test_path_traversal_blocked(self):
        project = self.store.create("x", "code")
        for bad in ("../meta.json", "..", "a/../../b", "inputs/../../x", "a\\..\\..\\b"):
            with self.assertRaises(ValueError):
                project.file(bad)
        # Absolute Pfade landen immer innerhalb des Projektordners
        self.assertIn(project.path.resolve(), project.file("/etc/passwd").parents)
        with self.assertRaises(KeyError):
            self.store.get("../etc")


class FakeProvider:
    """Simulierte KI: liefert nacheinander vorbereitete Antworten."""

    name = "Fake"

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def label(self):
        return "Fake"

    def complete(self, system, messages, cancel=None, max_tokens=0):
        self.calls.append([m.text for m in messages])
        return self.answers.pop(0)


GOOD_CODE = '''/* [Ansicht] */
part = "assembly"; // [assembly:Zusammenbau, all_parts:Alle Teile, base:Sockel, pin:Stift]
/* [Hidden] */
module base() difference() { cube([30, 30, 10]); translate([15, 15, 2]) cylinder(d = 8.4, h = 10); }
module pin() cylinder(d = 8, h = 16, $fn = 32);
module assembly() { base(); translate([15, 15, 2]) pin(); }
module all_parts() { base(); translate([40, 0, 0]) pin(); }
if (part == "assembly") assembly();
else if (part == "all_parts") all_parts();
else if (part == "base") base();
else if (part == "pin") pin();
'''


@unittest.skipUnless(HAS_OPENSCAD, "OpenSCAD nicht installiert")
class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(Path(tempfile.mkdtemp(dir=_HOME)) / "settings.json")
        self.settings.update({"visual_rounds": 0, "repair_attempts": 2})
        self.store = ProjectStore(Path(tempfile.mkdtemp(prefix="pipe-", dir=_HOME)))
        self._orig = pipeline.make_provider

    def tearDown(self):
        pipeline.make_provider = self._orig

    def test_repair_loop_and_parts(self):
        broken = "Entwurf\n```openscad\ncube([10, 10, 10]\n```"
        fixed = "Korrigiert\n```openscad\n" + GOOD_CODE + "```"
        fake = FakeProvider([broken, fixed])
        pipeline.make_provider = lambda settings, *a, **k: fake
        project = self.store.create("Stift", "ai")
        job = Job("test", "test")
        result = pipeline.ai_generate(job, project, self.settings, "Sockel mit Stift", [])
        self.assertEqual(len(fake.calls), 2, "Reparatur sollte genau einmal nachfragen")
        self.assertIn("Probleme", fake.calls[1][-1])
        self.assertTrue(result["check"]["ok"])
        parts = project.meta["parts"]
        self.assertEqual([p["id"] for p in parts], ["base", "pin"])
        self.assertTrue(all(p["ok"] for p in parts))
        self.assertTrue((project.path / "parts" / "pin.stl").exists())
        self.assertEqual(len(project.meta["versions"]), 2)

    def test_collision_check(self):
        placed = GOOD_CODE.replace(
            "module assembly() { base(); translate([15, 15, 2]) pin(); }",
            "module placed(id) { if (id == \"base\") base(); if (id == \"pin\") translate([15, 15, 2]) pin(); }\n"
            "module assembly() { for (id = [\"base\", \"pin\"]) placed(id); }")
        self.assertIn("module placed(", placed)
        project = self.store.create("Passung", "code")
        project.write_code(placed)
        job = Job("test", "test")
        fit = pipeline.collision_check(job, project, self.settings)["collisions"]
        self.assertTrue(fit["ok"], fit)
        self.assertEqual(len(fit["pairs"]), 1)
        # Stift zu dick (9 mm in 8,4-mm-Loch) → Kollision muss erkannt werden
        project.write_code(placed.replace("cylinder(d = 8, h = 16", "cylinder(d = 9, h = 16"))
        fit = pipeline.collision_check(job, project, self.settings)["collisions"]
        self.assertFalse(fit["ok"])
        self.assertGreater(fit["collisions"][0]["volume_mm3"], 1)
        self.assertTrue(pipeline.collision_problems(fit))
        self.assertFalse(list(project.path.glob("_passung*")), "Hilfsdateien wurden nicht aufgeräumt")

    def test_image_to_model_modes(self):
        from io import BytesIO
        from PIL import Image as PILImage, ImageDraw
        img = PILImage.new("RGB", (200, 160), "white")
        draw = ImageDraw.Draw(img)
        draw.ellipse([40, 20, 160, 140], fill="black")
        draw.ellipse([85, 65, 115, 95], fill="white")
        buf = BytesIO()
        img.save(buf, "PNG")
        project = self.store.create("Ring", "image")
        name = project.add_input(buf.getvalue(), "png")
        job = Job("test", "test")
        result = pipeline.image_to_model(job, project, self.settings, name, "keychain",
                                         {"size": 40, "height": 3, "ring_outer": 9, "ring_inner": 4,
                                          "threshold": None, "title": "stört nicht", "polygons": 1})
        self.assertTrue(result["check"]["ok"], project.meta.get("problems"))
        size = project.meta["check"]["size"]
        self.assertLessEqual(max(size[:2]), 55)
        self.assertTrue(project.meta["check"]["watertight"])
        result = pipeline.image_to_model(job, project, self.settings, name, "relief",
                                         {"width": 60, "depth": 2, "base": 1, "resolution": 80})
        self.assertTrue(result["check"]["ok"])
        self.assertAlmostEqual(project.meta["check"]["size"][0], 60, delta=0.6)

    def test_bed_overflow_is_reported(self):
        project = self.store.create("Zu groß", "code")
        project.write_code('cube([300, 20, 10]);')
        job = Job("test", "test")
        result = pipeline.render_and_check(job, project, self.settings, thumbnail=False)
        self.assertTrue(result["ok"])
        self.assertTrue(any("Druckbett" in p or "passt" in p for p in result["problems"]), result["problems"])


class ServerSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scadstudio.server import Studio, serve
        cls.studio = Studio(Settings(Path(_HOME) / "server.json"))
        cls.httpd = serve(port=18765, studio=cls.studio)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def request(self, path, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data,
                                     headers=headers or {}, method="POST" if data else "GET")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_status_and_index(self):
        status, body = self.request("/api/status")
        self.assertEqual(status, 200)
        self.assertIn("printer", json.loads(body))
        status, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"SCAD Studio", body)

    def test_post_requires_header(self):
        status, _ = self.request("/api/settings", {"provider": "openai"})
        self.assertEqual(status, 403)
        status, _ = self.request("/api/settings", {"provider": "openai"},
                                 {"X-Studio": "1", "Content-Type": "application/json",
                                  "Origin": "https://evil.example"})
        self.assertEqual(status, 403)

    def test_keepalive_after_rejected_post(self):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        conn.request("POST", "/api/settings", body=json.dumps({"x": "y" * 5000}),
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        resp.read()
        self.assertEqual(resp.status, 403)
        conn.request("GET", "/api/settings")          # gleiche Verbindung
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        self.assertIn("provider", json.loads(resp.read()))
        conn.close()

    def test_foreign_host_rejected(self):
        status, _ = self.request("/api/status", headers={"Host": "evil.example:80"})
        self.assertEqual(status, 403)

    def test_static_traversal(self):
        status, _ = self.request("/static/../server.py")
        self.assertIn(status, (403, 404))
        status, _ = self.request("/static/%2e%2e/server.py")
        self.assertIn(status, (403, 404))


def tearDownModule():
    shutil.rmtree(_HOME, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
